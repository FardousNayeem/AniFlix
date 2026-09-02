"""Harvest a training corpus from AniList.

This module exists so the model has something to learn from. It is *not* a
recommendation API: nothing here decides what to recommend. It downloads a
catalogue of public anime metadata and the crowd-voted "if you liked this,
try that" graph, and writes them to disk. Training (``ml/train.py``) reads
that file; the running site never does.

AniList's public GraphQL endpoint needs no key and no account. It rate limits
per minute and answers with 429 plus a ``Retry-After`` header when you push
too hard, so every request goes through ``_post`` which paces itself and
retries.
"""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

import requests

ENDPOINT = "https://graphql.anilist.co"

# One page carries 50 titles and their recommendation edges. Fewer requests
# beat smaller responses here: the rate limit is per request, not per byte.
PAGE_SIZE = 50
RECOMMENDATIONS_PER_TITLE = 25

# AniList publishes 90 requests/minute but degrades to 30 under load, and the
# response headers tell us which is in force. Start pessimistic.
MIN_SECONDS_BETWEEN_REQUESTS = 2.1

QUERY = """
query ($page: Int, $perPage: Int, $recs: Int, $year: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage currentPage }
    media(type: ANIME, sort: POPULARITY_DESC, isAdult: false, seasonYear: $year) {
      id
      title { romaji english }
      genres
      averageScore
      popularity
      episodes
      duration
      seasonYear
      format
      siteUrl
      coverImage { extraLarge large color }
      bannerImage
      studios(isMain: true) { nodes { name } }
      tags { name rank isGeneralSpoiler isMediaSpoiler }
      description(asHtml: false)
      recommendations(perPage: $recs, sort: RATING_DESC) {
        edges { node { rating mediaRecommendation { id } } }
      }
    }
  }
}
"""


@dataclass
class HarvestedTitle:
    """One anime as the corpus stores it."""

    anilist_id: int
    romaji: str
    english: str
    genres: list[str]
    tags: list[tuple[str, int]]
    description: str
    score: int | None
    popularity: int
    episodes: int | None
    duration: int | None
    year: int | None
    format: str
    studios: list[str]
    # Shown when a recommended title is not in the ANIFLIX catalogue: without
    # artwork an off-catalogue suggestion is just a line of text.
    cover_url: str
    # A wide landscape banner where AniList has one. The homepage spotlight is
    # a 16:9 box, and a 230x299 portrait cover stretched to fill it is a six
    # times upscale of the middle of somebody's face.
    banner_url: str
    cover_colour: str
    site_url: str
    # (target anilist id, net community votes). Votes can be negative when the
    # crowd disagrees with a suggestion; training drops those.
    recommendations: list[tuple[int, int]] = field(default_factory=list)


class HarvestError(RuntimeError):
    pass


def _post(session: requests.Session, variables: dict, *, attempts: int = 5) -> dict:
    """One GraphQL call, paced and retried.

    A 429 carries ``Retry-After`` in seconds; anything else transient gets a
    short exponential back-off. A GraphQL error body is fatal, because it means
    the query is wrong rather than the server busy.
    """
    delay = 2.0
    for attempt in range(1, attempts + 1):
        response = session.post(
            ENDPOINT, json={"query": QUERY, "variables": variables}, timeout=60
        )

        if response.status_code == 429:
            wait = float(response.headers.get("Retry-After", 60))
            time.sleep(wait + 1)
            continue

        if response.status_code >= 500:
            if attempt == attempts:
                raise HarvestError(f"AniList returned {response.status_code} five times")
            time.sleep(delay)
            delay *= 2
            continue

        if response.status_code != 200:
            raise HarvestError(f"AniList returned {response.status_code}: {response.text[:200]}")

        payload = response.json()
        if "errors" in payload:
            raise HarvestError(f"GraphQL error: {payload['errors'][:1]}")
        return payload["data"]["Page"]

    raise HarvestError("AniList stayed rate limited")


def _clean_description(raw: str | None) -> str:
    """AniList descriptions carry a little HTML even with asHtml: false."""
    if not raw:
        return ""
    text = raw.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
    for tag in ("<i>", "</i>", "<b>", "</b>", "<em>", "</em>", "<strong>", "</strong>"):
        text = text.replace(tag, " ")
    return " ".join(text.split())


def _parse_title(node: dict) -> HarvestedTitle:
    recommendations: list[tuple[int, int]] = []
    for edge in node.get("recommendations", {}).get("edges") or []:
        inner = edge.get("node") or {}
        target = inner.get("mediaRecommendation")
        if not target:
            # The crowd suggested a manga, or a title AniList has since removed.
            continue
        recommendations.append((int(target["id"]), int(inner.get("rating") or 0)))

    tags = [
        (tag["name"], int(tag.get("rank") or 0))
        for tag in node.get("tags") or []
        # Spoiler tags describe the ending, not the appeal. They would teach the
        # model to match on plot twists the viewer has not seen yet.
        if not tag.get("isGeneralSpoiler") and not tag.get("isMediaSpoiler")
    ]

    title = node.get("title") or {}
    return HarvestedTitle(
        anilist_id=int(node["id"]),
        romaji=title.get("romaji") or "",
        english=title.get("english") or "",
        genres=list(node.get("genres") or []),
        tags=tags,
        description=_clean_description(node.get("description")),
        score=node.get("averageScore"),
        popularity=int(node.get("popularity") or 0),
        episodes=node.get("episodes"),
        duration=node.get("duration"),
        year=node.get("seasonYear"),
        format=node.get("format") or "",
        studios=[s["name"] for s in (node.get("studios") or {}).get("nodes") or []],
        cover_url=(
            (node.get("coverImage") or {}).get("extraLarge")
            or (node.get("coverImage") or {}).get("large")
            or ""
        ),
        banner_url=node.get("bannerImage") or "",
        cover_colour=(node.get("coverImage") or {}).get("color") or "",
        site_url=node.get("siteUrl") or "",
        recommendations=recommendations,
    )


class _Pacer:
    """Keeps requests at the published rate without a sleep in the caller."""

    def __init__(self) -> None:
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < MIN_SECONDS_BETWEEN_REQUESTS:
            time.sleep(MIN_SECONDS_BETWEEN_REQUESTS - elapsed)
        self._last = time.monotonic()


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "ANIFLIX-recommender-training/1.0"
    return session


def harvest(*, pages: int, progress=None) -> Iterator[HarvestedTitle]:
    """Yield titles from the most popular ``pages * PAGE_SIZE`` anime.

    Popularity order is deliberate: the recommendation graph is only dense
    where people actually watch things, and a model trained on the long tail
    of nine-vote obscurities learns noise.

    Note the ceiling. AniList will not paginate a single sorted query past
    5,000 results, so this tops out at page 100 however many you ask for —
    which is why ``harvest_by_year`` exists.
    """
    session = _session()
    pacer = _Pacer()

    for page in range(1, pages + 1):
        pacer.wait()
        data = _post(
            session,
            {"page": page, "perPage": PAGE_SIZE, "recs": RECOMMENDATIONS_PER_TITLE, "year": None},
        )
        for node in data.get("media") or []:
            yield _parse_title(node)

        if progress is not None:
            progress(page, pages)
        if not (data.get("pageInfo") or {}).get("hasNextPage"):
            return


def harvest_by_year(
    *, first_year: int, last_year: int, pages_per_year: int, progress=None
) -> Iterator[HarvestedTitle]:
    """Yield titles a year at a time, to get past the 5,000-result ceiling.

    Each year is its own sorted query with its own pagination window, so
    partitioning the catalogue by release year turns one capped query into
    sixty uncapped ones. Years run newest first: if the harvest is cut short,
    what it has is the part with the densest recommendation graph.

    Titles are de-duplicated by id, because a year query and a later one can
    both return a long-running show.
    """
    session = _session()
    pacer = _Pacer()
    seen: set[int] = set()
    years = list(range(last_year, first_year - 1, -1))

    for index, year in enumerate(years, start=1):
        for page in range(1, pages_per_year + 1):
            pacer.wait()
            data = _post(
                session,
                {
                    "page": page,
                    "perPage": PAGE_SIZE,
                    "recs": RECOMMENDATIONS_PER_TITLE,
                    "year": year,
                },
            )
            for node in data.get("media") or []:
                title = _parse_title(node)
                if title.anilist_id in seen:
                    continue
                seen.add(title.anilist_id)
                yield title

            if not (data.get("pageInfo") or {}).get("hasNextPage"):
                break

        if progress is not None:
            progress(index, len(years), year, len(seen))


def write_corpus(titles: list[HarvestedTitle], path: Path) -> Path:
    """Store the corpus gzipped: it is a build input, not something to read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "anilist",
        "endpoint": ENDPOINT,
        "count": len(titles),
        "titles": [asdict(title) for title in titles],
    }
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def read_corpus(path: Path) -> list[HarvestedTitle]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    # Defaults keep a corpus harvested before a field existed readable, so an
    # older download does not have to be thrown away to add one.
    defaults = {"cover_url": "", "banner_url": "", "cover_colour": "", "site_url": ""}
    return [
        HarvestedTitle(
            **{
                **defaults,
                **row,
                "tags": [tuple(tag) for tag in row["tags"]],
                "recommendations": [tuple(edge) for edge in row["recommendations"]],
            }
        )
        for row in payload["titles"]
    ]
