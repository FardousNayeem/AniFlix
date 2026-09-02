"""Train the taste model and write the artifact the site serves.

Build step. Needs numpy and the harvested corpus; the running site needs
neither. Run it after `harvest_corpus`, and again whenever titles are added
to the catalogue.
"""

from __future__ import annotations

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.recommender.questions import all_terms
from apps.streaming.models import Anime


class Command(BaseCommand):
    help = (
        "Train the anime recommender on the harvested corpus and write the "
        "runtime model artifact."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--corpus",
            default=str(settings.RECOMMENDER_CORPUS_PATH),
            help="Gzipped corpus from `harvest_corpus`.",
        )
        parser.add_argument(
            "--max-titles", type=int, default=settings.RECOMMENDER_MAX_TITLES,
            help=(
                "Train on at most this many titles, most popular first "
                f"(default {settings.RECOMMENDER_MAX_TITLES})."
            ),
        )
        parser.add_argument(
            "--out",
            default=str(settings.RECOMMENDER_MODEL_PATH),
            help="Where to write the trained model.",
        )

    def handle(self, *args, **options):
        try:
            import numpy  # noqa: F401
        except ModuleNotFoundError as error:
            raise CommandError(
                "Training needs numpy: pip install -r requirements-ml.txt. "
                "The site itself does not — it reads the artifact this writes."
            ) from error

        # Imported here so the ORM and settings are ready, and so a site that
        # never trains never imports numpy.
        from apps.recommender.ml.catalogue import (
            build_reference_titles,
            build_title_entries,
            resolve_catalogue_ids,
            write_model,
        )
        from apps.recommender.ml.harvest import read_corpus
        from apps.recommender.ml.train import train

        corpus_path = Path(options["corpus"])
        out_path = Path(options["out"])
        if not corpus_path.exists():
            raise CommandError(
                f"No corpus at {corpus_path}. Run `manage.py harvest_corpus` first."
            )

        started = time.monotonic()
        self.stdout.write(f"Reading corpus from {corpus_path}…")
        corpus = read_corpus(corpus_path)
        self.stdout.write(f"  {len(corpus)} titles in the corpus")

        cap = options["max_titles"]
        if cap and len(corpus) > cap:
            corpus = sorted(corpus, key=lambda title: -title.popularity)[:cap]
            self.stdout.write(f"  training on the {cap} most popular")

        self.stdout.write("Training…")
        model = train(corpus, log=lambda line: self.stdout.write(line))

        self._check_questionnaire(model)

        self._link_catalogue(corpus)

        titles = build_title_entries(model=model, corpus=corpus)
        self.stdout.write(f"Shipping {len(titles)} titles the model can recommend.")

        references = build_reference_titles(model=model, corpus=corpus)
        self.stdout.write(
            f"Shipping {len(references)} well-known title names so "
            '"something like X" resolves to a learned vector.'
        )

        write_model(
            model=model,
            titles=titles,
            references=references,
            path=out_path,
            trained_from=corpus_path.name,
        )

        size_mb = out_path.stat().st_size / 1_048_576
        carried = Anime.objects.exclude(anilist_id=None).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {out_path} ({size_mb:.2f} MB) in {time.monotonic() - started:.0f}s. "
                f"{carried} of them are in the ANIFLIX catalogue; the rest are "
                "offered as titles we do not carry."
            )
        )

    def _link_catalogue(self, corpus) -> None:
        """Give catalogue rows their AniList id, so we know what we stock.

        Rows added by `seed_catalogue` already have one. This is for the ones
        that predate it: without an id a title we carry looks, to the widget,
        exactly like one we do not.
        """
        # Imported here, not at module scope, so a site that never trains never
        # pulls numpy in through this module's import chain.
        from apps.recommender.ml.catalogue import resolve_catalogue_ids

        rows = self._catalogue_rows()
        if not rows:
            self.stdout.write(
                self.style.WARNING(
                    "The catalogue is empty. The recommender will still work, "
                    "but every answer will be a title we do not carry. Run "
                    "`manage.py seed_catalogue` to stock it."
                )
            )
            return

        unlinked = [row for row in rows if not row["anilist_id"]]
        if not unlinked:
            self.stdout.write(f"All {len(rows)} catalogue titles are already linked.")
            return

        self.stdout.write(f"Linking {len(unlinked)} catalogue titles to AniList…")
        resolved = resolve_catalogue_ids(
            rows=unlinked, corpus=corpus, log=lambda line: self.stdout.write(line)
        )
        for slug, anilist_id in resolved.items():
            # A duplicate would mean two of our rows are the same show; leave
            # the second unlinked rather than break the unique constraint.
            if Anime.objects.filter(anilist_id=anilist_id).exists():
                continue
            Anime.objects.filter(slug=slug).update(anilist_id=anilist_id)
        self.stdout.write(f"  linked {len(resolved)}")

    def _check_questionnaire(self, model) -> None:
        """Every answer must be a term the model actually knows.

        Without this a renamed tag turns an answer into a no-op that still
        looks like it works, which is the worst kind of broken.
        """
        known = set(model.vocabulary.terms)
        missing = sorted(term for term in all_terms() if term not in known)
        if missing:
            raise CommandError(
                "These questionnaire terms are not in the trained vocabulary, so "
                "answering with them would change nothing:\n  "
                + "\n  ".join(missing)
                + "\nFix apps/recommender/questions.py, or widen the vocabulary."
            )
        self.stdout.write("  every questionnaire term is in the vocabulary")

    def _catalogue_rows(self) -> list[dict]:
        queryset = (
            Anime.objects.annotate(episode_total=Count("episodes", distinct=True))
            .prefetch_related("genres")
            .order_by("name")
        )
        return [
            {
                "slug": anime.slug,
                "name": anime.name,
                "synopsis": anime.synopsis,
                "studio": anime.studio,
                "release_year": anime.release_year,
                "episode_count": anime.episode_total,
                "anilist_id": anime.anilist_id,
                "genres": [genre.name for genre in anime.genres.all()],
            }
            for anime in queryset
        ]
