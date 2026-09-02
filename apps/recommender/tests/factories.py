"""A tiny trained model, written to disk, so tests never need numpy.

The real artifact is 0.9 MB of learned weights. Tests do not want it: they
want a model whose geometry they chose, so an assertion about ranking is an
assertion about the scoring rules rather than about what MyAnimeList voters
happened to think. This builds one with hand-placed vectors.
"""

from __future__ import annotations

import base64
import gzip
import json
from array import array
from pathlib import Path

DIMENSIONS = 4

# Four orthogonal directions, one per axis, so "dark" and "funny" are as far
# apart as the space allows and a ranking can be reasoned about by hand.
AXES = {
    "dark": [1.0, 0.0, 0.0, 0.0],
    "funny": [0.0, 1.0, 0.0, 0.0],
    "action": [0.0, 0.0, 1.0, 0.0],
    "calm": [0.0, 0.0, 0.0, 1.0],
}

# The vocabulary terms the questionnaire actually uses, pointed at an axis.
TERM_AXIS = {
    "genre:thriller": "dark",
    "genre:psychological": "dark",
    "genre:horror": "dark",
    "tag:philosophy": "dark",
    "word:dark": "dark",
    "genre:comedy": "funny",
    "tag:parody": "funny",
    "tag:slapstick": "funny",
    "tag:satire": "funny",
    "word:comedy": "funny",
    "genre:action": "action",
    "tag:super-power": "action",
    "tag:martial-arts": "action",
    "word:battle": "action",
    "word:fight": "action",
    "genre:drama": "dark",
    "tag:coming-of-age": "dark",
    "word:loss": "dark",
    "word:death": "dark",
    "genre:mystery": "dark",
    "tag:detective": "dark",
    "tag:crime": "dark",
    "word:mystery": "dark",
    "word:truth": "dark",
    "genre:slice-of-life": "calm",
    "tag:iyashikei": "calm",
    "word:everyday": "calm",
    "tag:episodic": "calm",
    "genre:romance": "calm",
    "tag:love-triangle": "calm",
    "genre:ecchi": "calm",
    "tag:nudity": "calm",
    "tag:female-harem": "calm",
    "tag:gore": "dark",
    "tag:body-horror": "dark",
    "tag:war": "dark",
    "length:short": "calm",
    "length:season": "calm",
    "length:long": "action",
    "era:classic": "action",
    "era:recent": "calm",
    "word:pirate": "action",
    "word:funny": "funny",
    "genre:sports": "action",
}


QUANTISE = 127.0


def _pack(rows: list[list[float]]) -> tuple[str, list[float]]:
    """The same int8 packing the trainer writes, without needing numpy."""
    packed = array("b")
    scales = []
    for row in rows:
        scale = max((abs(value) for value in row), default=0.0)
        scales.append(scale)
        divisor = scale or 1.0
        packed.extend(
            max(-127, min(127, int(round(value / divisor * QUANTISE)))) for value in row
        )
    return base64.b64encode(packed.tobytes()).decode("ascii"), scales


def build_payload(*, titles=None, references=None) -> dict:
    vocabulary = sorted(TERM_AXIS)
    rows = titles if titles is not None else default_titles()
    projection, projection_scales = _pack([AXES[TERM_AXIS[term]] for term in vocabulary])
    vectors, vector_scales = _pack([row.pop("vector") for row in rows])
    return {
        "version": 3,
        "trained_at": "2026-09-02T00:00:00+00:00",
        "trained_from": "test-fixture",
        "dimensions": DIMENSIONS,
        "quantise_scale": QUANTISE,
        "metrics": {"recall_at_10": 0.25},
        "vocabulary": vocabulary,
        "idf": [1.0] * len(vocabulary),
        "projection": projection,
        "projection_scales": projection_scales,
        "vectors": vectors,
        "vector_scales": vector_scales,
        "titles": rows,
        "reference_titles": references or {},
    }


# AniList ids for the fixture titles, so a test can stock some and not others.
IDS = {
    "grim": 101,
    "bleak": 102,
    "funny": 103,
    "punch": 104,
    "calm": 105,
}


def default_titles() -> list[dict]:
    """One title per axis, plus a second dark one.

    The dark pair matters: they sit at the same point in taste space and
    differ only in their tags, which is the only way to prove that an "avoid"
    answer reorders a ranking rather than lowering every score together.
    """
    return [
        title(IDS["grim"], "Grim Thing", "dark", ["genre:thriller", "genre:psychological"],
              episodes=11, year=2014),
        title(IDS["bleak"], "Bleak Thing", "dark", ["genre:thriller", "tag:philosophy"],
              episodes=24, year=2014),
        title(IDS["funny"], "Funny Thing", "funny", ["genre:comedy", "tag:parody"],
              episodes=201, year=2006),
        title(IDS["punch"], "Punch Thing", "action", ["genre:action", "tag:super-power"],
              episodes=12, year=1999),
        title(IDS["calm"], "Calm Thing", "calm", ["genre:slice-of-life", "tag:iyashikei"],
              episodes=13, year=2018),
    ]


def title(anilist_id, name, axis, terms, *, episodes, year, neighbours=()):
    from apps.recommender.text import era_facet, length_facet

    return {
        "id": anilist_id,
        "name": name,
        "vector": AXES[axis],
        "terms": terms,
        "episodes": episodes,
        "year": year,
        "score": 80,
        "popularity": 1000,
        "cover": f"https://example.test/{anilist_id}.jpg",
        "colour": "#112233",
        "neighbours": list(neighbours),
        "facets": {
            "length": (length_facet(episodes) or ":").split(":")[-1] or None,
            "era": (era_facet(year) or ":").split(":")[-1] or None,
        },
    }


def write_model(path: Path, **kwargs) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(build_payload(**kwargs), handle)
    return path
