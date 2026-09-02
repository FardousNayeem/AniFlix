"""Download the training corpus. Build step, never run by the site."""

from __future__ import annotations

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.recommender.ml.harvest import (
    HarvestError,
    PAGE_SIZE,
    harvest,
    harvest_by_year,
    write_corpus,
)

DEFAULT_PAGES = 100
DEFAULT_FIRST_YEAR = 1963
DEFAULT_PAGES_PER_YEAR = 8


class Command(BaseCommand):
    help = (
        "Harvest public anime metadata and the community recommendation graph "
        "from AniList into a local corpus file, for training the recommender."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--pages",
            type=int,
            default=DEFAULT_PAGES,
            help=(
                f"Pages of {PAGE_SIZE} titles to fetch (default {DEFAULT_PAGES}). "
                "AniList publishes 90 requests a minute but drops to 30 or "
                "lower under load, and answers 429 with a Retry-After of a "
                "minute when it does, so a large run can take far longer than "
                "the estimate it prints."
            ),
        )
        parser.add_argument(
            "--by-year", action="store_true",
            help=(
                "Harvest a year at a time instead of one popularity-sorted "
                "list. Slower, but the only way past AniList's 5,000-result "
                "pagination ceiling."
            ),
        )
        parser.add_argument(
            "--first-year", type=int, default=DEFAULT_FIRST_YEAR,
            help=f"Oldest release year to harvest (default {DEFAULT_FIRST_YEAR}).",
        )
        parser.add_argument(
            "--pages-per-year", type=int, default=DEFAULT_PAGES_PER_YEAR,
            help=(
                f"Pages of {PAGE_SIZE} to take from each year "
                f"(default {DEFAULT_PAGES_PER_YEAR})."
            ),
        )
        parser.add_argument(
            "--out",
            default=str(settings.RECOMMENDER_CORPUS_PATH),
            help="Where to write the gzipped corpus.",
        )

    def handle(self, *args, **options):
        out = Path(options["out"])
        started = time.monotonic()

        if options["by_year"]:
            last_year = timezone.now().year
            first_year = options["first_year"]
            per_year = options["pages_per_year"]
            requests_at_most = (last_year - first_year + 1) * per_year
            self.stdout.write(
                f"Harvesting {first_year}-{last_year}, up to {per_year} pages a year "
                f"(~{requests_at_most * 2.1 / 60:.0f} min at the public rate limit)…"
            )

            def progress(index, total, year, count):
                self.stdout.write(f"  {year}  ({index}/{total} years, {count} titles)")
                self.stdout.flush()

            stream = harvest_by_year(
                first_year=first_year,
                last_year=last_year,
                pages_per_year=per_year,
                progress=progress,
            )
        else:
            pages = options["pages"]
            self.stdout.write(
                f"Harvesting up to {pages * PAGE_SIZE} titles from AniList "
                f"(~{pages * 2.1 / 60:.1f} min at the public rate limit)…"
            )

            def progress(page: int, total: int) -> None:
                if page % 5 == 0 or page == total:
                    self.stdout.write(f"  page {page}/{total}  ({page * PAGE_SIZE} titles)")
                    # A long run takes long enough that somebody will watch it.
                    # Without this the block buffer holds every line back and a
                    # healthy harvest looks like a hung one.
                    self.stdout.flush()

            stream = harvest(pages=pages, progress=progress)

        # Collected as we go, so a rate limit part-way through costs the last
        # few pages rather than the whole download.
        titles = []
        try:
            for harvested in stream:
                titles.append(harvested)
        except HarvestError as error:
            if len(titles) < PAGE_SIZE * 10:
                raise CommandError(
                    f"{error}. Only {len(titles)} titles in hand, too few to train on."
                ) from error
            self.stderr.write(
                self.style.WARNING(
                    f"AniList stopped early ({error}). Keeping the {len(titles)} "
                    "titles already downloaded — rerun to extend the corpus."
                )
            )
        except KeyboardInterrupt:
            if not titles:
                raise
            self.stderr.write(
                self.style.WARNING(f"Interrupted. Keeping {len(titles)} titles.")
            )

        if not titles:
            raise CommandError("AniList returned nothing; refusing to write an empty corpus.")

        write_corpus(titles, out)
        edges = sum(len(title.recommendations) for title in titles)
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {len(titles)} titles and {edges} recommendation edges to {out} "
                f"in {time.monotonic() - started:.0f}s."
            )
        )
