"""Fill the catalogue from the harvested corpus.

The site launched with nine titles, which is too few to browse and too few to
recommend from. This takes the most popular titles out of the training corpus
and creates real catalogue rows for them, with artwork.

It is deliberately separate from training. Training decides what a title *is*;
this decides what the site *carries*. Run it before `train_recommender` so the
model places everything the catalogue now holds.

Idempotent: a title already in the catalogue is left alone, matched on its
AniList id first and its name second.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.recommender.genres import GENRES, TAG_GENRE_MIN_RANK, TAG_TO_GENRE
from apps.recommender.ml.harvest import read_corpus
from apps.recommender.text import normalise_phrase
from apps.recommender.images import optimise
from apps.streaming.models import Anime, Episode, Genre

DEFAULT_COUNT = 240

# Artwork is re-encoded on the way in. AniList serves a banner at whatever
# size the licensor supplied — often 1900px and a quarter of a megabyte — and
# ours is rendered dimmed behind a gradient with text over it. Storing the
# original is paying full price for pixels nobody will ever see. Pillow is
# already a dependency, so this is free.
MAX_WIDTH = {"poster": 460, "backdrop": 1280}
JPEG_QUALITY = 82
# Enough of an episode list for a title page to look finished, without
# inventing a thousand rows for One Piece.
EPISODE_STUBS = 3
POSTER_TIMEOUT = 20


class Command(BaseCommand):
    help = "Create catalogue titles and the full genre list from the harvested corpus."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count", type=int, default=DEFAULT_COUNT,
            help=f"How many titles to add (default {DEFAULT_COUNT}).",
        )
        parser.add_argument(
            "--corpus", default=str(settings.RECOMMENDER_CORPUS_PATH),
            help="Gzipped corpus from `harvest_corpus`.",
        )
        parser.add_argument(
            "--no-posters", action="store_true",
            help="Skip downloading cover art. Faster, and leaves placeholder tiles.",
        )
        parser.add_argument(
            "--genres-only", action="store_true",
            help="Create the genre list and stop, adding no titles.",
        )

    def handle(self, *args, **options):
        # Respected so a test, or a scripted deploy, can run this quietly.
        self.quiet = options.get("verbosity", 1) == 0
        created_genres = self._seed_genres()
        self._say(
            f"Genres: {created_genres} created, {Genre.objects.count()} in total."
        )
        if options["genres_only"]:
            return

        corpus_path = Path(options["corpus"])
        if not corpus_path.exists():
            raise CommandError(
                f"No corpus at {corpus_path}. Run `manage.py harvest_corpus` first."
            )

        self._say(f"Reading {corpus_path}…")
        corpus = read_corpus(corpus_path)

        wanted = self._choose(corpus, options["count"])
        added = 0
        if wanted:
            self._say(f"Adding {len(wanted)} titles…")
            genres = {genre.name: genre for genre in Genre.objects.all()}
            for index, title in enumerate(wanted, start=1):
                if self._create(title, genres) is None:
                    continue
                added += 1
                if index % 25 == 0:
                    self._say(f"  {index}/{len(wanted)}")
                    self._flush()
            self._say(self.style.SUCCESS(f"Created {added} titles."))
        else:
            # Not a reason to stop: `--count 0` is how you backfill artwork
            # for titles that are already here.
            self._say("No new titles to add.")

        if not options["no_posters"]:
            self._download_posters()

        self._say(
            self.style.SUCCESS(
                f"Catalogue now holds {Anime.objects.count()} titles across "
                f"{Genre.objects.count()} genres. Run `train_recommender` next."
            )
        )

    def _say(self, message) -> None:
        if not self.quiet:
            self.stdout.write(message)

    def _flush(self) -> None:
        if not self.quiet:
            self.stdout.flush()

    # ----------------------------------------------------------------- genres
    def _seed_genres(self) -> int:
        """Create every genre the site offers, used or not."""
        existing = {genre.name for genre in Genre.objects.all()}
        created = 0
        for name, description in GENRES.items():
            if name in existing:
                # Fill in a description for a genre that arrived from the
                # legacy import without one, but never overwrite an edited one.
                Genre.objects.filter(name=name, description="").update(
                    description=description
                )
                continue
            Genre.objects.create(name=name, description=description)
            created += 1
        return created

    # ----------------------------------------------------------------- titles
    def _choose(self, corpus, count: int) -> list:
        """The most popular titles we do not already carry."""
        have_ids = set(
            Anime.objects.exclude(anilist_id=None).values_list("anilist_id", flat=True)
        )
        have_names = {
            normalise_phrase(name) for name in Anime.objects.values_list("name", flat=True)
        }

        chosen = []
        for title in sorted(corpus, key=lambda item: -item.popularity):
            if len(chosen) >= count:
                break
            if title.anilist_id in have_ids:
                continue
            # A title needs these to be a catalogue row at all: the model
            # requires a year, and a page with no synopsis is not worth having.
            if not title.year or not title.description:
                continue
            name = title.english or title.romaji
            if not name or normalise_phrase(name) in have_names:
                continue
            have_names.add(normalise_phrase(name))
            chosen.append(title)
        return chosen

    @transaction.atomic
    def _create(self, title, genres: dict[str, Genre]) -> Anime | None:
        name = title.english or title.romaji
        anime = Anime.objects.create(
            name=name[:200],
            studio=(title.studios[0] if title.studios else "")[:200],
            release_year=title.year,
            status=self._status(title),
            synopsis=title.description,
            anilist_id=title.anilist_id,
        )
        anime.genres.set(self._genres_for(title, genres))

        for number in range(1, min(title.episodes or 1, EPISODE_STUBS) + 1):
            Episode.objects.create(
                anime=anime,
                number=number,
                title=f"Episode {number}",
                # No source yet. The player already handles an episode with
                # nothing behind it, and inventing a link would be worse.
                video_url="",
            )
        return anime

    def _status(self, title) -> str:
        if title.episodes:
            return Anime.Status.COMPLETED
        # No episode count on AniList usually means it has not finished airing.
        return Anime.Status.ONGOING

    def _genres_for(self, title, genres: dict[str, Genre]) -> list[Genre]:
        names = {genre for genre in title.genres if genre in genres}
        for tag, rank in title.tags:
            if rank < TAG_GENRE_MIN_RANK:
                continue
            mapped = TAG_TO_GENRE.get(tag)
            if mapped and mapped in genres:
                names.add(mapped)
        return [genres[name] for name in names]

    # ---------------------------------------------------------------- artwork
    ART_QUERY = """
    query ($ids: [Int]) {
      Page(page: 1, perPage: 50) {
        media(id_in: $ids, type: ANIME) {
          id
          coverImage { extraLarge large }
          bannerImage
        }
      }
    }
    """

    def _fetch_art(self, session, ids: list[int]) -> dict[int, dict]:
        """Current cover and banner URLs for a batch of titles.

        Asked for here rather than taken from the corpus because the corpus
        may predate banner collection, and because a spotlight needs the wide
        banner while a poster tile needs the tall cover.
        """
        art: dict[int, dict] = {}
        for start in range(0, len(ids), 50):
            batch = ids[start : start + 50]
            response = session.post(
                "https://graphql.anilist.co",
                json={"query": self.ART_QUERY, "variables": {"ids": batch}},
                timeout=POSTER_TIMEOUT,
            )
            if response.status_code != 200:
                continue
            for node in (response.json().get("data") or {}).get("Page", {}).get("media") or []:
                cover = node.get("coverImage") or {}
                art[int(node["id"])] = {
                    "poster": cover.get("extraLarge") or cover.get("large") or "",
                    "backdrop": node.get("bannerImage") or "",
                }
            time.sleep(2.1)
        return art

    def _download_posters(self) -> None:
        """Fetch cover art for titles that have none.

        Downloaded rather than hotlinked: a catalogue that breaks when
        somebody else's CDN changes a path is not a catalogue.
        """
        pending = [
            anime
            for anime in Anime.objects.exclude(anilist_id=None)
            if not anime.poster or not anime.backdrop
        ]
        if not pending:
            return

        session = requests.Session()
        session.headers["User-Agent"] = "ANIFLIX-catalogue-seeder/1.0"

        self._say(f"Looking up artwork for {len(pending)} titles…")
        art = self._fetch_art(session, [anime.anilist_id for anime in pending])

        self._say("Downloading…")
        saved = banners = failed = 0
        for index, anime in enumerate(pending, start=1):
            urls = art.get(anime.anilist_id) or {}
            if not anime.poster and urls.get("poster"):
                if self._save_image(session, anime, "poster", urls["poster"]):
                    saved += 1
                else:
                    failed += 1
            # A banner is optional: plenty of titles simply do not have one,
            # and the spotlight falls back to the poster for those.
            if not anime.backdrop and urls.get("backdrop"):
                if self._save_image(session, anime, "backdrop", urls["backdrop"]):
                    banners += 1
            if index % 25 == 0:
                self._say(f"  {index}/{len(pending)}")
                self._flush()

        message = f"Saved {saved} covers and {banners} wide banners"
        if failed:
            message += f", {failed} could not be fetched"
        self._say(self.style.SUCCESS(message + "."))

    def _save_image(self, session, anime: Anime, field: str, url: str) -> bool:
        try:
            response = session.get(url, timeout=POSTER_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException:
            return False

        content = optimise(response.content, MAX_WIDTH[field])
        getattr(anime, field).save(
            f"{anime.slug}-{field}.jpg", ContentFile(content), save=True
        )
        # The CDN is somebody else's, and this is a bulk job.
        time.sleep(0.05)
        return True
