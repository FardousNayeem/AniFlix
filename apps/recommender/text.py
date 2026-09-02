"""Tokenising and term naming, shared by training and by the running site.

This lives outside ``ml/`` on purpose. Training imports it, and so does the
request path — which must never import numpy. Keeping the tokeniser in one
place is also the only way a free-text query gets cut into the same terms the
model was fitted on; two copies of this that drift apart would silently stop
matching.
"""

from __future__ import annotations

import re

ENGLISH_STOPWORDS = frozenset(
    """
    a about after again against all also am an and another any are as at back be because been
    before being below between both but by came can cannot come could did do does doing down
    during each even every few for from further get got had has have having he her here hers
    herself him himself his how however i if in into is it its itself just like made make many
    may me might more most much must my myself never new no nor not now of off on once one only
    or other our ours ourselves out over own same she should since so some source such take than
    that the their theirs them themselves then there these they this those through to too under
    until up upon us very was way we well were what when where which while who whom why will with
    within would you your yours yourself yourselves
    want watch watching something show shows really thing things kind sort feel feels
    """.split()
)

# Words that carry meaning in a title but are pure boilerplate in a synopsis.
# "Death Note" is a title; "based on the light novel" is noise. Filtering the
# second list out of descriptions and *not* out of names is what lets
# "something like Death Note" reach Death Note's own embedding.
DESCRIPTION_BOILERPLATE = frozenset(
    """
    note anime series season episode episodes story stories adaptation based manga
    novel light written original air aired airing announced source note
    """.split()
)

STOPWORDS = ENGLISH_STOPWORDS | DESCRIPTION_BOILERPLATE

TOKEN_RE = re.compile(r"[a-z][a-z'-]{2,}")

# Terms are namespaced so a genre called "Action" and a description word
# "action" cannot collide into one feature.
TERM_KINDS = ("genre", "tag", "studio", "era", "length", "format", "word")


def word_tokens(text: str, *, boilerplate: bool = True) -> list[str]:
    """Tokens worth learning from.

    ``boilerplate=False`` keeps synopsis filler words, for text where they are
    not filler — a title, or a query naming one.
    """
    blocked = STOPWORDS if boilerplate else ENGLISH_STOPWORDS
    return [
        token
        for token in TOKEN_RE.findall((text or "").lower())
        if token not in blocked and len(token) > 2
    ]


def normalise_term(kind: str, value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return f"{kind}:{slug}" if slug else ""


def era_facet(year: int | None) -> str | None:
    if not year:
        return None
    if year < 2000:
        return "era:classic"
    if year < 2013:
        return "era:modern"
    return "era:recent"


def length_facet(episodes: int | None) -> str | None:
    """Three buckets, because that is the resolution a viewer asks in."""
    if not episodes:
        return None
    if episodes <= 16:
        return "length:short"
    if episodes <= 60:
        return "length:season"
    return "length:long"


# Terms whose slug does not read as English on its own.
_LABEL_OVERRIDES = {
    "length:short": "a short watch",
    "length:season": "one season long",
    "length:long": "a long run",
    "era:classic": "a pre-2000 classic",
    "era:modern": "from the 2000s",
    "era:recent": "recent",
    "genre:slice-of-life": "slice of life",
    "genre:sci-fi": "sci-fi",
    "tag:iyashikei": "gentle and healing",
    "tag:coming-of-age": "coming of age",
    "tag:super-power": "super powers",
    "tag:surreal-comedy": "surreal comedy",
    "tag:episodic": "episodic",
}


def term_label(term: str) -> str:
    """A term as a person would say it, for explaining a recommendation."""
    if term in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[term]
    _, _, value = term.partition(":")
    return value.replace("-", " ")


# People do not type genre names. They type "funny", "scary", "something to
# cry to". Every one of those is a genre the model knows perfectly well under
# a different word, and without this table the strongest signal in a typed
# sentence lands on nothing.
#
# Single words, matched against tokens so "sad" does not fire inside
# "sadly". Everything here is checked against the trained vocabulary by
# `manage.py train_recommender`, so a term that stops existing fails the build.
WORD_ALIASES = {
    # comedy
    "funny": "genre:comedy", "hilarious": "genre:comedy", "laugh": "genre:comedy",
    "silly": "genre:comedy", "humour": "genre:comedy", "humor": "genre:comedy",
    "comedic": "genre:comedy", "lighthearted": "genre:comedy", "gags": "tag:slapstick",
    # drama
    "sad": "genre:drama", "cry": "genre:drama", "emotional": "genre:drama",
    "tearjerker": "genre:drama", "heartbreaking": "genre:drama",
    "depressing": "genre:drama", "moving": "genre:drama", "bittersweet": "genre:drama",
    # horror
    "scary": "genre:horror", "creepy": "genre:horror", "terrifying": "genre:horror",
    "frightening": "genre:horror", "spooky": "genre:horror", "gruesome": "tag:gore",
    "bloody": "tag:gore",
    # calm
    "cute": "genre:slice-of-life", "wholesome": "genre:slice-of-life",
    "relaxing": "tag:iyashikei", "chill": "genre:slice-of-life",
    "cozy": "tag:iyashikei", "cosy": "tag:iyashikei", "comfy": "tag:iyashikei",
    "gentle": "tag:iyashikei", "healing": "tag:iyashikei",
    # thriller / psychological
    "tense": "genre:thriller", "suspense": "genre:thriller",
    "suspenseful": "genre:thriller", "gripping": "genre:thriller",
    "cerebral": "genre:psychological", "psychological": "genre:psychological",
    "psycological": "genre:psychological", "twisted": "genre:psychological",
    "philosophical": "tag:philosophy",
    # action
    "fights": "genre:action", "fighting": "genre:action", "battles": "genre:action",
    "martial": "tag:martial-arts", "superhero": "tag:super-power",
    "superpowers": "tag:super-power", "superpower": "tag:super-power",
    # worlds
    "robots": "genre:mecha", "mechs": "genre:mecha", "mecha": "genre:mecha",
    "magic": "genre:fantasy", "magical": "genre:fantasy", "wizards": "genre:fantasy",
    "space": "genre:sci-fi", "futuristic": "genre:sci-fi", "cyberpunk": "tag:cyberpunk",
    "dystopian": "tag:dystopian", "isekai": "tag:isekai",
    # people
    "romantic": "genre:romance", "love": "genre:romance",
    "ghosts": "genre:supernatural", "demons": "tag:demons",
    "detective": "tag:detective", "whodunit": "genre:mystery",
    "investigation": "genre:mystery", "murder": "tag:crime", "crime": "tag:crime",
    "sports": "genre:sports", "idol": "genre:music", "band": "genre:music",
    # Shape, not subject. These are the words "not too long" turns on, and
    # without them a stated length preference in free text does nothing.
    "long": "length:long", "lengthy": "length:long", "endless": "length:long",
    "short": "length:short", "quick": "length:short", "brief": "length:short",
    "old": "era:classic", "classic": "era:classic", "retro": "era:classic",
    "recent": "era:recent", "modern": "era:recent", "new": "era:recent",
}

# Multi-word phrases, matched as substrings because their slug is unguessable.
PHRASE_ALIASES = {
    "slice of life": "genre:slice-of-life",
    "science fiction": "genre:sci-fi",
    "sci fi": "genre:sci-fi",
    "scifi": "genre:sci-fi",
    "coming of age": "tag:coming-of-age",
    "super power": "tag:super-power",
    "martial arts": "tag:martial-arts",
    "slice-of-life": "genre:slice-of-life",
    "post apocalyptic": "tag:post-apocalyptic",
    "body horror": "tag:body-horror",
    "love triangle": "tag:love-triangle",
    "giant robots": "genre:mecha",
    "mind bending": "genre:psychological",
    "make me laugh": "genre:comedy",
    "cheer me up": "genre:comedy",
    "want to cry": "genre:drama",
    "feel good": "genre:slice-of-life",
}

# Kept as one mapping for the build-time vocabulary check.
TEXT_ALIASES = {**PHRASE_ALIASES, **WORD_ALIASES}


def raw_tokens(text: str) -> list[str]:
    """Every word, stopwords kept, for building n-grams to match term slugs."""
    return TOKEN_RE.findall((text or "").lower())


# Release-order noise a catalogue row carries and a viewer never types.
_TITLE_NOISE = re.compile(
    r"\b(season\s*(one|two|three|1|2|3|i{1,3})|part\s*\d+|1st season|2nd season"
    r"|tv|the animation)\b"
)


def normalise_phrase(text: str) -> str:
    """A title reduced to comparable words.

    Used for both sides of a title lookup — the names baked into the model and
    the sentence somebody types — so the two cannot normalise differently and
    quietly stop matching.
    """
    lowered = (text or "").lower().replace("&", "and")
    lowered = _TITLE_NOISE.sub(" ", lowered)
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


# A viewer says what they do not want as often as what they do. Reading
# "anything but comedy" as a request for comedy is worse than reading nothing.
NEGATION_WORDS = frozenset(
    """
    no not dont don't never without except avoid hate hating hated nothing
    none neither nor cannot can't isn't aren't wasn't
    """.split()
)

# "but" only negates in these phrases; "dark but funny" wants both.
NEGATION_PHRASES = ("anything but", "everything but", "nothing but", "other than")

_CLAUSE_SPLIT = re.compile(r"[,;:.!?]+")


def clauses(text: str) -> list[tuple[str, bool]]:
    """Split a sentence into clauses, each flagged as negated or not.

    Punctuation is the boundary, because that is where an English sentence
    changes its mind: "no romance please, something dark" is a refusal and a
    request, and scoring it as one bag of words gets both halves wrong.
    """
    out: list[tuple[str, bool]] = []
    for part in _CLAUSE_SPLIT.split((text or "").lower()):
        part = part.strip()
        if not part:
            continue
        words = set(TOKEN_RE.findall(part)) | set(part.split())
        negated = bool(words & NEGATION_WORDS) or any(
            phrase in part for phrase in NEGATION_PHRASES
        )
        out.append((part, negated))
    return out
