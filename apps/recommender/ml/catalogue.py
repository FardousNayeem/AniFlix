"""Export the trained model as the artifact the site reads.

Every title the model learned a vector for ships, not only the ones ANIFLIX
carries. Whether we stock a recommendation is a fact about our database on
the day somebody asks, so it is answered at request time by looking the
AniList id up — not baked into the model and left to go stale.

This module also matches our own older rows to their AniList entries, so
titles that predate the catalogue seeder can still be recognised as ones we
have rather than offered as if we did not.
"""

from __future__ import annotations

import base64
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..questions import FACET_ERA, FACET_LENGTH
from ..text import era_facet, length_facet, normalise_phrase, normalise_term
from .harvest import HarvestedTitle
from .train import TrainedModel

ARTIFACT_VERSION = 3
FLOAT_PRECISION = 4

# Vectors ship as signed bytes, not as JSON numbers.
#
# Written out as text, fifteen thousand 96-dimension vectors and a projection
# matrix the same size come to 21 MB and take over a second to parse. Every
# one of those numbers is a coordinate in a unit-length vector being compared
# by cosine, where the eighth decimal place has never once changed a ranking.
# Quantised to int8 with a scale factor the same data is 2.4 MB and loads with
# a single frombytes, and the largest coordinate error it can introduce is
# under 0.004 — two orders of magnitude below the gap between adjacent
# results.
QUANTISE_SCALE = 127.0

# Both sides of every name comparison go through the shared normaliser, so a
# reference baked into the artifact and a sentence typed into the widget
# cannot disagree about what "Attack on Titan" looks like.
_normalise_name = normalise_phrase


def _tokens(name: str) -> set[str]:
    return set(_normalise_name(name).split())


def match_catalogue_title(
    *, name: str, year: int | None, corpus: list[HarvestedTitle]
) -> tuple[HarvestedTitle | None, float]:
    """Find the AniList title our catalogue row refers to.

    Scores an exact normalised-name hit far above a token overlap, then uses
    release year to break ties — which is what separates Hunter x Hunter 1999
    from the 2011 remake, two titles with identical names and different fans.
    """
    wanted = _normalise_name(name)
    wanted_tokens = _tokens(name)
    if not wanted_tokens:
        return None, 0.0

    best: tuple[HarvestedTitle | None, float] = (None, 0.0)
    for candidate in corpus:
        names = [candidate.romaji, candidate.english]
        score = 0.0
        for other in names:
            if not other:
                continue
            normalised = _normalise_name(other)
            if not normalised:
                continue
            if normalised == wanted:
                score = max(score, 1.0)
                continue
            overlap = wanted_tokens & _tokens(other)
            if overlap:
                union = wanted_tokens | _tokens(other)
                score = max(score, 0.75 * len(overlap) / len(union))

        if score <= 0:
            continue

        if year and candidate.year:
            distance = abs(candidate.year - year)
            # Same year is a strong confirmation; a decade away is a warning.
            score += 0.08 if distance == 0 else -min(distance, 12) * 0.012
        # Among equally-named candidates prefer the one people actually watch,
        # which is also the one with a denser recommendation neighbourhood.
        score += min(candidate.popularity, 400_000) / 400_000 * 0.03

        if score > best[1]:
            best = (candidate, score)

    # Below this a "match" is two shows sharing the word "the".
    return best if best[1] >= 0.45 else (None, best[1])


def _round(values) -> list[float]:
    return [round(float(value), FLOAT_PRECISION) for value in values]


def _quantise(matrix: np.ndarray) -> tuple[str, list[float]]:
    """Pack a matrix of row vectors as base64 int8, one scale factor per row.

    Per row rather than one scale for the whole matrix: the projection's rows
    differ in magnitude by orders of magnitude, and a single shared scale
    would flatten the small ones to zero.
    """
    rows = np.asarray(matrix, dtype=np.float32)
    scales = np.abs(rows).max(axis=1)
    safe = np.where(scales > 0, scales, 1.0)
    quantised = np.rint(rows / safe[:, None] * QUANTISE_SCALE)
    packed = np.clip(quantised, -127, 127).astype(np.int8)
    return (
        base64.b64encode(packed.tobytes()).decode("ascii"),
        [round(float(scale), 6) for scale in scales],
    )


def build_title_entries(*, model: TrainedModel, corpus: list[HarvestedTitle]) -> list[dict]:
    """One artifact entry per title the model learned a vector for.

    Everything in the graph ships, not just the titles ANIFLIX carries. A
    recommender that can only answer with the nine things on the shelf is a
    filter, not a recommendation; whether we happen to stock a title is a
    question for the request, and the request answers it by looking the
    AniList id up in our own database.
    """
    by_id = {title.anilist_id: title for title in corpus}
    entries: list[dict] = []

    for position, anilist_id in enumerate(model.corpus_ids):
        title = by_id.get(anilist_id)
        if title is None:
            continue
        entries.append(
            {
                "id": anilist_id,
                "name": title.english or title.romaji,
                "year": title.year,
                "episodes": title.episodes,
                "score": title.score,
                "popularity": title.popularity,
                "cover": title.cover_url,
                "colour": title.cover_colour,
                "terms": _defining_terms(title),
                "neighbours": _neighbours(title, corpus, limit=2),
                "row": position,
                "facets": {
                    FACET_LENGTH: _facet_value(length_facet(title.episodes)),
                    FACET_ERA: _facet_value(era_facet(title.year)),
                },
            }
        )

    return entries


def _facet_value(facet: str | None) -> str | None:
    return facet.split(":")[-1] if facet else None


def resolve_catalogue_ids(
    *, rows: list[dict], corpus: list[HarvestedTitle], log=print
) -> dict[str, int]:
    """Work out which AniList title each un-linked catalogue row is.

    Titles added by `seed_catalogue` already carry their id. This is for the
    ones that arrived before that existed — the original nine — so they can be
    recognised as available rather than recommended as if we did not have them.
    """
    resolved: dict[str, int] = {}
    for row in rows:
        if row.get("anilist_id"):
            continue
        matched, confidence = match_catalogue_title(
            name=row["name"], year=row["release_year"], corpus=corpus
        )
        if matched is None:
            log(f"    {row['name']:<30} no AniList match; it stays unlinked")
            continue
        resolved[row["slug"]] = matched.anilist_id
        log(
            f"    {row['name']:<30} -> {matched.english or matched.romaji} "
            f"({matched.year}, confidence {confidence:.2f})"
        )
    return resolved


def _defining_terms(title: HarvestedTitle, *, limit: int = 12) -> list[str]:
    """The terms that describe a title, for matching and for explaining.

    The length and era facets are added *after* the cap, not before it. They
    used to be appended to the list and truncated away on any title with a
    dozen strong tags, which silently disarmed "not too long" — the refusal
    had nothing to match against on exactly the long-running shows it was
    meant to exclude.
    """
    subject = [normalise_term("genre", genre) for genre in title.genres]
    ranked = sorted(title.tags, key=lambda tag: -tag[1])
    subject += [normalise_term("tag", name) for name, rank in ranked if rank >= 60]

    terms = [term for term in dict.fromkeys(subject) if term][:limit]
    terms += [
        facet
        for facet in (era_facet(title.year), length_facet(title.episodes))
        if facet
    ]
    return list(dict.fromkeys(terms))


def _neighbours(
    title: HarvestedTitle, corpus: list[HarvestedTitle], *, limit: int
) -> list[str]:
    """The titles the community most often recommends alongside this one.

    Flavour for the explanation, not an input to the ranking.
    """
    names = {other.anilist_id: (other.english or other.romaji) for other in corpus}
    ordered = sorted(title.recommendations, key=lambda edge: -edge[1])
    out: list[str] = []
    for target_id, votes in ordered:
        if votes <= 0:
            break
        name = names.get(target_id)
        if name:
            out.append(name)
        if len(out) >= limit:
            break
    return out


def facet_of(entry: dict) -> dict[str, str | None]:
    """The metadata facets a stated preference is checked against."""
    return {
        FACET_LENGTH: (length_facet(entry.get("episodes")) or "").split(":")[-1] or None,
        FACET_ERA: (era_facet(entry.get("year")) or "").split(":")[-1] or None,
    }


# How many of the best-known titles ship with their own vector, so that
# "something like Cowboy Bebop" is answered by that title's actual place in
# the recommendation graph instead of by the words in its name.
REFERENCE_TITLES = 800


def build_reference_titles(
    *, model: TrainedModel, corpus: list[HarvestedTitle]
) -> dict[str, dict]:
    """Normalised title name -> its learned vector, for the popular titles.

    Capped by popularity: the point is to recognise the titles a viewer would
    name, and nobody asks for "something like" a show with 200 viewers. Names
    shorter than four characters are dropped — "Air" and "Gto" match inside
    ordinary sentences and would hijack the query.
    """
    index = {anilist_id: i for i, anilist_id in enumerate(model.corpus_ids)}
    ranked = sorted(
        (title for title in corpus if title.anilist_id in index),
        key=lambda title: -title.popularity,
    )[:REFERENCE_TITLES]

    table: dict[str, dict] = {}
    for title in ranked:
        vector = _round(model.item_embedding[index[title.anilist_id]])
        for name in (title.english, title.romaji):
            key = _normalise_name(name)
            if len(key) < 4 or key in table:
                continue
            table[key] = {"label": title.english or title.romaji, "vector": vector}
    return table


def write_model(
    *,
    model: TrainedModel,
    titles: list[dict],
    references: dict[str, dict],
    path: Path,
    trained_from: str,
) -> Path:
    """Write the runtime artifact.

    The vocabulary and its IDF, the projection matrix, and one vector per
    title. The co-occurrence graph, the corpus and numpy all stay behind in
    the build.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Only the rows actually exported, in the order the titles reference.
    rows = [entry.pop("row") for entry in titles]
    vectors, vector_scales = _quantise(model.item_embedding[rows])
    projection, projection_scales = _quantise(model.projection)

    payload = {
        "version": ARTIFACT_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trained_from": trained_from,
        "dimensions": int(model.projection.shape[1]),
        "quantise_scale": QUANTISE_SCALE,
        "metrics": model.metrics,
        "vocabulary": model.vocabulary.terms,
        "idf": [round(value, FLOAT_PRECISION) for value in model.vocabulary.idf],
        "projection": projection,
        "projection_scales": projection_scales,
        "vectors": vectors,
        "vector_scales": vector_scales,
        "titles": titles,
        "reference_titles": references,
    }

    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    return path
