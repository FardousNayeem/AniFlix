"""Load the trained model and score a request against it.

Everything here runs in the request path, so it is deliberately plain: gzip
and json from the standard library, and arithmetic over lists. No numpy, no
model server, no network. The expensive half of this feature already happened
in ``manage.py train_recommender``.

The artifact is read once and kept in memory, keyed on the file's mtime and
size, so a redeploy that ships a new model picks it up without a restart and
without stat-ing the file on every request.
"""

from __future__ import annotations

import base64
import difflib
import gzip
import json
import logging
import math
import threading
from array import array
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from .text import (
    PHRASE_ALIASES,
    WORD_ALIASES,
    clauses,
    normalise_phrase,
    raw_tokens,
    term_label,
    word_tokens,
)

logger = logging.getLogger(__name__)

# Three signals decide a ranking, and each answers a different question.
#
#   taste   - "what do people who like this sort of thing also watch?"
#             The learned space, from the recommendation graph.
#   content - "does this title literally carry what was asked for?"
#             Direct overlap with the title's own genres and tags.
#   facet   - "does it fit the stated facts?" Length and era.
#
# Taste leads because it is the part that generalises: it knows Psycho-Pass
# and Terror in Resonance belong together without being told. But it averages
# a whole neighbourhood, so on its own it can miss a title that plainly says
# what the viewer asked for, which is what content is here to catch.
TASTE_WEIGHT = 0.5
CONTENT_WEIGHT = 0.3
FACET_WEIGHT = 0.2

# A title carrying something the viewer asked to avoid loses this much per
# unit of avoid weight. Large enough to bury a match, not so large that one
# soft dislike wipes out an otherwise perfect fit.
AVOID_PENALTY = 0.45

# Already on their list, or already rated: they have seen it. Recommending it
# is not wrong, just useless, and with a small catalogue dropping it entirely
# would empty the results.
SEEN_PENALTY = 0.25

# A nudge towards titles people have actually heard of. Deliberately small: it
# breaks ties between equally good matches rather than deciding them, because
# the point of a recommender is to surface things you have not seen. Without
# it the tail wins every time — nine thousand obscure OVAs each match some
# query slightly better than the famous title everyone means.
POPULARITY_WEIGHT = 0.12

# Somebody who says "something like Cowboy Bebop" knows about Cowboy Bebop.
NAMED_PENALTY = 1.0


class ModelUnavailable(RuntimeError):
    """No usable artifact on disk."""


@dataclass(frozen=True)
class Title:
    """One anime the model can recommend, in or out of our catalogue."""

    anilist_id: int
    name: str
    terms: frozenset[str]
    # The same terms as a unit-length IDF-weighted vector, built once at load
    # rather than per request: with twelve thousand titles, rebuilding these
    # on every ask is the whole cost of the feature.
    term_vector: dict[str, float]
    facets: dict[str, str | None]
    episodes: int | None
    year: int | None
    cover: str
    colour: str
    neighbours: tuple[str, ...]
    community_score: int | None
    popularity: int


@dataclass(frozen=True)
class ReferenceTitle:
    """A well-known title the viewer might name, and where it sits."""

    label: str
    vector: tuple[float, ...]


@dataclass(frozen=True)
class TasteModel:
    dimensions: int
    idf: dict[str, float]
    # Vocabulary term -> its row in the packed projection matrix.
    terms: dict[str, int]
    # Both matrices are int8, flattened, with one scale factor per row. See
    # ml/catalogue.py for why they are not floats.
    projection: array
    projection_scales: tuple[float, ...]
    quantise_scale: float
    items: tuple[Title, ...]
    # log1p of the most popular title, so the prior above is a 0..1 fraction.
    popularity_ceiling: float
    # All item vectors end to end in one int8 buffer, with a scale per title.
    # Fifteen thousand 96-dimension tuples would be ~35 MB of Python float
    # objects per worker; this is 1.4 MB.
    vectors: array
    vector_scales: tuple[float, ...]
    trained_at: str
    metrics: dict = field(default_factory=dict)
    references: dict[str, ReferenceTitle] = field(default_factory=dict)

    def vector_of(self, index: int) -> list[float]:
        """One title's vector, unpacked back to floats."""
        start = index * self.dimensions
        factor = self.vector_scales[index] / self.quantise_scale
        return [value * factor for value in self.vectors[start : start + self.dimensions]]

    def project(self, term_weights: dict[str, float]) -> list[float]:
        """Bag of terms -> a point in taste space.

        TF-IDF with sublinear term frequency and L2 normalisation, matching
        ``ml/train.vectorise`` exactly, then multiplied through the projection
        matrix. Only terms the model knows contribute; an unknown word is
        silently ignored, which is the right behaviour for free text.
        """
        weighted: dict[str, float] = {}
        for term, weight in term_weights.items():
            if weight <= 0 or term not in self.terms:
                continue
            frequency = 1.0 + math.log(weight) if weight > 1 else weight
            weighted[term] = frequency * self.idf.get(term, 1.0)

        norm = math.sqrt(sum(value * value for value in weighted.values()))
        if norm == 0:
            return [0.0] * self.dimensions

        dimensions = self.dimensions
        projection = self.projection
        vector = [0.0] * dimensions
        for term, value in weighted.items():
            row = self.terms[term]
            # The row's own scale, folded in with the query weight so the
            # inner loop stays a single multiply-add per dimension.
            factor = (value / norm) * self.projection_scales[row] / self.quantise_scale
            base = row * dimensions
            for index in range(dimensions):
                vector[index] += factor * projection[base + index]

        return _unit(vector)

    def known_terms(self, terms) -> set[str]:
        return {term for term in terms if term in self.terms}

    def term_vector(self, term_weights: dict[str, float]) -> dict[str, float]:
        return _term_vector(term_weights, self.idf)

    def text_terms(
        self, text: str, *, word_scale: float = 0.8, structured_scale: float = 1.4
    ) -> dict[str, float]:
        """Read a typed sentence as model terms.

        Description words alone under-read a query like "something
        psychological and short": the strongest signal in that sentence is a
        genre name, and a genre term is worth far more to the projection than
        the same string appearing in somebody's synopsis. So the text is
        scanned for term names as well as tokenised — n-grams up to three
        words, plus a small alias table for the ones nobody would type as a
        slug.
        """
        terms: dict[str, float] = {}

        # boilerplate=False: the viewer may be naming a title, and "Death
        # Note" must not lose half its name to the synopsis filter.
        for token in word_tokens(text, boilerplate=False):
            term = self._fold(f"word:{token}")
            if term:
                terms[term] = terms.get(term, 0.0) + word_scale

        words = raw_tokens(text)
        for size in (1, 2, 3):
            for start in range(len(words) - size + 1):
                slug = "-".join(words[start : start + size])
                for kind in ("genre", "tag"):
                    term = self._fold(f"{kind}:{slug}")
                    if term:
                        terms[term] = terms.get(term, 0.0) + structured_scale

        # Everyday words for a genre: "funny" is comedy, "scary" is horror.
        # Matched per token so "sad" does not fire inside "sadly".
        for token in set(words):
            term = WORD_ALIASES.get(token)
            if term and term in self.terms:
                terms[term] = terms.get(term, 0.0) + structured_scale

        # Phrases are matched against the full normalised text, not the token
        # join: the tokeniser drops words under three letters, which would
        # turn "cheer me up" into "cheer please" and never match.
        lowered = normalise_phrase(text)
        for phrase, term in PHRASE_ALIASES.items():
            if phrase in lowered and term in self.terms:
                terms[term] = terms.get(term, 0.0) + structured_scale

        # Last resort, for words nothing above recognised: a near-miss on a
        # genre or tag name is almost always a typo for it.
        if not any(term.startswith(("genre:", "tag:")) for term in terms):
            for token in word_tokens(text, boilerplate=False):
                if self._fold(f"word:{token}"):
                    continue
                corrected = self._correct(token)
                if corrected:
                    terms[corrected] = terms.get(corrected, 0.0) + structured_scale

        return terms

    def find_references(self, text: str, *, limit: int = 2) -> list[ReferenceTitle]:
        """Titles named in the text, longest name first.

        Substring matching over a normalised copy, which is enough because the
        table only holds names of four characters or more. Longest first so
        "Attack on Titan" is not swallowed by a shorter name inside it.
        """
        if not self.references:
            return []

        haystack = f" {normalise_phrase(text)} "
        found: list[ReferenceTitle] = []
        for name in sorted(self.references, key=len, reverse=True):
            needle = f" {name} "
            if needle not in haystack:
                continue
            reference = self.references[name]
            if all(reference.label != seen.label for seen in found):
                found.append(reference)
            # Take the match out of the sentence so a shorter title sitting
            # inside it cannot match too: "Steins;Gate" is not also "Gate".
            haystack = haystack.replace(needle, " ")
            if len(found) >= limit:
                break
        return found

    def read_text(self, text: str) -> tuple[dict[str, float], dict[str, float]]:
        """Read a sentence as (what they want, what they do not).

        Each clause is read on its own, so one sentence can do both. Only
        genres and tags are collected from a negated clause: "not too long"
        is a real constraint, but the loose description words around a
        refusal say nothing reliable about what to avoid.
        """
        wanted: dict[str, float] = {}
        avoided: dict[str, float] = {}

        for clause, negated in clauses(text):
            terms = self.text_terms(clause)
            if not negated:
                for term, weight in terms.items():
                    wanted[term] = wanted.get(term, 0.0) + weight
                continue
            for term, weight in terms.items():
                if term.startswith(("genre:", "tag:", "length:", "era:")):
                    avoided[term] = max(avoided.get(term, 0.0), min(1.0, weight))

        return wanted, avoided

    def _named_slugs(self) -> tuple[str, ...]:
        """Genre and tag slugs, for spell-correcting a typed word against.

        Built once per model and cached on the instance. Only the structured
        names: correcting against the whole 10,000-word vocabulary would turn
        every unrecognised noun into whichever term it happens to resemble.
        """
        cached = getattr(self, "_slug_cache", None)
        if cached is None:
            cached = tuple(
                term.partition(":")[2]
                for term in self.terms
                if term.startswith(("genre:", "tag:"))
            )
            object.__setattr__(self, "_slug_cache", cached)
        return cached

    def _correct(self, token: str) -> str | None:
        """The genre or tag a misspelt word was reaching for, if it is close.

        "psycological" should not be silently dropped when "psychological" is
        the strongest signal in the sentence. The cutoff is deliberately tight
        — a loose one turns "monster" into "monsters" into somebody's tag and
        invents an answer the viewer never asked for.
        """
        if len(token) < 6:
            return None
        close = difflib.get_close_matches(token, self._named_slugs(), n=1, cutoff=0.84)
        if not close:
            return None
        for kind in ("genre", "tag"):
            term = f"{kind}:{close[0]}"
            if term in self.terms:
                return term
        return None

    def _fold(self, term: str) -> str | None:
        """The term as the model knows it, or None.

        Training never stemmed, so the vocabulary holds whichever of
        "pirate"/"pirates" was common enough to survive. Folding the plural at
        query time costs one dictionary lookup and stops a typed "pirates"
        from missing a term the model definitely has.
        """
        if term in self.terms:
            return term
        for suffix, replacement in (("ies", "y"), ("es", ""), ("s", "")):
            if term.endswith(suffix) and len(term) - len(suffix) > 6:
                candidate = term[: -len(suffix)] + replacement
                if candidate in self.terms:
                    return candidate
        return None


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def cosine(left, right) -> float:
    return sum(a * b for a, b in zip(left, right))


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
_lock = threading.Lock()
_cache: tuple[tuple, TasteModel] | None = None


def _parse(payload: dict) -> TasteModel:
    vocabulary = payload["vocabulary"]
    dimensions = int(payload["dimensions"])
    quantise = float(payload.get("quantise_scale") or 127.0)

    projection = _unpack(payload["projection"])
    projection_scales = tuple(payload["projection_scales"])
    if len(projection) != len(vocabulary) * dimensions or len(projection_scales) != len(vocabulary):
        raise ModelUnavailable("Vocabulary and projection matrix disagree in size.")

    idf = dict(zip(vocabulary, payload["idf"]))

    vectors = _unpack(payload["vectors"])
    vector_scales = tuple(payload["vector_scales"])

    entries = payload.get("titles") or []
    if len(vectors) != len(entries) * dimensions or len(vector_scales) != len(entries):
        raise ModelUnavailable("Title list and vector block disagree in size.")

    items: list[Title] = []
    for entry in entries:
        terms = frozenset(entry.get("terms") or [])
        items.append(
            Title(
                anilist_id=int(entry["id"]),
                name=entry.get("name") or "",
                terms=terms,
                term_vector=_term_vector(dict.fromkeys(terms, 1.0), idf),
                facets=entry.get("facets") or {},
                episodes=entry.get("episodes"),
                year=entry.get("year"),
                cover=entry.get("cover") or "",
                colour=entry.get("colour") or "",
                neighbours=tuple(entry.get("neighbours") or []),
                community_score=entry.get("score"),
                popularity=int(entry.get("popularity") or 0),
            )
        )

    if not items:
        raise ModelUnavailable("The artifact contains no scorable titles.")

    references = {
        name: ReferenceTitle(label=row["label"], vector=tuple(row["vector"]))
        for name, row in (payload.get("reference_titles") or {}).items()
        if row.get("vector")
    }

    return TasteModel(
        dimensions=dimensions,
        idf=idf,
        terms={term: row for row, term in enumerate(vocabulary)},
        projection=projection,
        projection_scales=projection_scales,
        quantise_scale=quantise,
        items=tuple(items),
        popularity_ceiling=math.log1p(max((item.popularity for item in items), default=0)),
        vectors=vectors,
        vector_scales=vector_scales,
        trained_at=payload.get("trained_at", ""),
        metrics=payload.get("metrics", {}),
        references=references,
    )


def _unpack(blob: str) -> array:
    """A base64 int8 matrix back into a flat signed-byte array."""
    packed = array("b")
    packed.frombytes(base64.b64decode(blob))
    return packed


def _term_vector(term_weights: dict[str, float], idf: dict[str, float]) -> dict[str, float]:
    """A bag of terms as a unit-length IDF-weighted sparse vector."""
    weighted = {
        term: (1.0 + math.log(weight) if weight > 1 else weight) * idf.get(term, 1.0)
        for term, weight in term_weights.items()
        if weight > 0 and term in idf
    }
    norm = math.sqrt(sum(value * value for value in weighted.values()))
    if norm == 0:
        return {}
    return {term: value / norm for term, value in weighted.items()}


def load_model(path: Path | None = None) -> TasteModel:
    """The trained model, read once and cached until the file changes."""
    global _cache

    location = Path(path or settings.RECOMMENDER_MODEL_PATH)
    try:
        stat = location.stat()
    except OSError as error:
        raise ModelUnavailable(
            f"No trained model at {location}. Run `manage.py harvest_corpus` "
            "then `manage.py train_recommender`."
        ) from error

    fingerprint = (str(location), stat.st_mtime_ns, stat.st_size)
    cached = _cache
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    with _lock:
        cached = _cache
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        try:
            with gzip.open(location, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as error:
            raise ModelUnavailable(f"Could not read {location}: {error}") from error

        model = _parse(payload)
        _cache = (fingerprint, model)
        return model


def reset_cache() -> None:
    """Drop the in-memory model. Tests that swap artifacts need this."""
    global _cache
    with _lock:
        _cache = None


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
@dataclass
class Query:
    """What the viewer asked for, in the model's own terms."""

    terms: dict[str, float] = field(default_factory=dict)
    avoid: dict[str, float] = field(default_factory=dict)
    facets: dict[str, str] = field(default_factory=dict)
    free_text: str = ""
    seen_ids: frozenset[int] = frozenset()
    # Titles the viewer named, e.g. "something like Cowboy Bebop".
    references: list = field(default_factory=list)
    named: set = field(default_factory=set)

    def add_terms(self, terms: dict[str, float], *, scale: float = 1.0) -> None:
        for term, weight in terms.items():
            self.terms[term] = self.terms.get(term, 0.0) + weight * scale

    def add_avoid(self, terms: dict[str, float]) -> None:
        for term, weight in terms.items():
            self.avoid[term] = max(self.avoid.get(term, 0.0), weight)

    def add_free_text(self, text: str, model: "TasteModel", *, scale: float = 1.0) -> None:
        """Fold a typed sentence into the query.

        A sentence can carry two different things. Words are read as terms and
        projected like any other answer. A *named title* is stronger evidence
        than any word: the model already knows where that title sits, so its
        own vector is used rather than a guess assembled from its name.
        """
        self.free_text = (text or "").strip()
        wanted, avoided = model.read_text(self.free_text)
        self.add_terms(wanted, scale=scale)
        self.add_avoid(avoided)
        self.references = model.find_references(self.free_text)
        self.named = {reference.label for reference in self.references}


@dataclass(frozen=True)
class Scored:
    item: Title
    fit: float
    taste: float
    content: float
    facet: float
    penalty: float
    matched_terms: tuple[str, ...]
    matched_facets: tuple[str, ...]
    avoided_terms: tuple[str, ...]
    already_seen: bool


def score(model: TasteModel, query: Query, *, limit: int | None = None) -> list[Scored]:
    """Rank every title the model knows against one query, best first.

    ``fit`` blends the learned similarity with how well the title satisfies
    the stated facts, then subtracts what the viewer asked to avoid. It is
    clamped to 0..1 so it can be shown as a percentage without inventing a
    scale the model does not have.

    ``limit`` keeps only the best few. Building a ``Scored`` for twelve
    thousand titles costs far more than scoring them does, so the cut happens
    against a running threshold rather than afterwards.
    """
    taste_vector = _taste_vector(model, query)
    has_taste = any(taste_vector)
    query_terms = model.term_vector(query.terms)
    wanted = model.known_terms(query.terms)
    facets = query.facets
    avoid = query.avoid

    dimensions = model.dimensions
    vectors = model.vectors
    scales = model.vector_scales
    quantise = model.quantise_scale
    named = query.named
    popularity_ceiling = model.popularity_ceiling
    ranked: list[tuple[float, int]] = []

    for index, item in enumerate(model.items):
        if has_taste:
            base = index * dimensions
            total = 0.0
            for offset in range(dimensions):
                total += taste_vector[offset] * vectors[base + offset]
            # One rescale per title rather than per dimension.
            taste = max(0.0, total * scales[index] / quantise)
        else:
            taste = 0.0

        item_terms = item.term_vector
        content = 0.0
        for term, weight in query_terms.items():
            other = item_terms.get(term)
            if other:
                content += weight * other

        if facets:
            hits = sum(
                1 for facet, value in facets.items() if item.facets.get(facet) == value
            )
            fit = (
                TASTE_WEIGHT * taste
                + CONTENT_WEIGHT * content
                + FACET_WEIGHT * (hits / len(facets))
            )
        else:
            # Unstated facets are not a failed test: drop the weight and
            # renormalise, so a query with no length or era preference is not
            # capped below a perfect score.
            fit = (TASTE_WEIGHT * taste + CONTENT_WEIGHT * content) / (
                TASTE_WEIGHT + CONTENT_WEIGHT
            )

        if popularity_ceiling:
            fit += POPULARITY_WEIGHT * (
                math.log1p(item.popularity) / popularity_ceiling
            )

        if avoid:
            fit -= AVOID_PENALTY * sum(
                weight for term, weight in avoid.items() if term in item.terms
            )
        if item.anilist_id in query.seen_ids:
            fit -= SEEN_PENALTY
        if named and item.name in named:
            # They named it. They want something *like* it.
            fit -= NAMED_PENALTY

        # Kept even at or below zero. A query that is nothing but a refusal
        # has no positive signal anywhere, and returning an empty list for
        # "anything but comedy" would be the wrong answer to a fair question.
        ranked.append((fit, index))

    ranked.sort(key=lambda pair: (-pair[0], model.items[pair[1]].name))
    if limit is not None:
        ranked = ranked[:limit]

    return [
        _describe(model, index, fit, query, query_terms, wanted, taste_vector)
        for fit, index in ranked
    ]


def _describe(
    model: TasteModel,
    index: int,
    fit: float,
    query: Query,
    query_terms: dict[str, float],
    wanted: set[str],
    taste_vector: list[float],
) -> Scored:
    """The full record for one title, built only for the ones being returned."""
    item = model.items[index]
    taste = max(0.0, cosine(taste_vector, model.vector_of(index))) if any(taste_vector) else 0.0
    content = sum(
        weight * item.term_vector.get(term, 0.0) for term, weight in query_terms.items()
    )
    matched_facets = [
        f"{facet}:{value}"
        for facet, value in query.facets.items()
        if item.facets.get(facet) == value
    ]
    avoided = tuple(sorted(term for term in query.avoid if term in item.terms))
    already_seen = item.anilist_id in query.seen_ids
    penalty = AVOID_PENALTY * sum(query.avoid[term] for term in avoided)
    if already_seen:
        penalty += SEEN_PENALTY

    return Scored(
        item=item,
        fit=max(0.0, min(1.0, fit)),
        taste=taste,
        content=content,
        facet=len(matched_facets) / len(query.facets) if query.facets else 0.0,
        penalty=penalty,
        matched_terms=tuple(sorted(wanted & item.terms)),
        matched_facets=tuple(matched_facets),
        avoided_terms=avoided,
        already_seen=already_seen,
    )


# When a title is named, its own vector is most of the answer. The rest of the
# sentence still counts — "something like Death Note but funnier" means both.
REFERENCE_WEIGHT = 0.65


def _taste_vector(model: TasteModel, query: Query) -> list[float]:
    """Where in taste space this query points."""
    projected = model.project(query.terms)
    if not query.references:
        return projected

    centroid = [0.0] * model.dimensions
    for reference in query.references:
        for index, value in enumerate(reference.vector):
            centroid[index] += value / len(query.references)

    if not any(projected):
        return _unit(centroid)

    return _unit(
        [
            REFERENCE_WEIGHT * centroid[index] + (1 - REFERENCE_WEIGHT) * projected[index]
            for index in range(model.dimensions)
        ]
    )


def explain(scored: Scored, *, limit: int = 3) -> list[str]:
    """Short human reasons for one recommendation.

    Only things that actually moved the score, in the order they moved it,
    so the explanation cannot claim credit the ranking did not give.
    """
    reasons = [term_label(term) for term in scored.matched_terms[:limit]]
    reasons += [term_label(facet) for facet in scored.matched_facets]

    if not reasons and scored.item.terms:
        # Nothing overlapped: say what the title is rather than nothing.
        reasons = [term_label(term) for term in sorted(scored.item.terms)[:2]]

    # Dedupe, keep order.
    return list(dict.fromkeys(reasons))[: limit + 1]
