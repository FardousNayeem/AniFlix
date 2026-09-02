"""Stocking the catalogue from the corpus."""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.recommender.genres import GENRES
from apps.recommender.ml.harvest import HarvestedTitle, write_corpus
from apps.streaming.models import Anime, Episode, Genre


def title(anilist_id, *, name, genres=(), tags=(), year=2015, episodes=12,
          description="A test synopsis.", popularity=1000):
    return HarvestedTitle(
        anilist_id=anilist_id,
        romaji=name,
        english=name,
        genres=list(genres),
        tags=list(tags),
        description=description,
        score=75,
        popularity=popularity,
        episodes=episodes,
        duration=24,
        year=year,
        format="TV",
        studios=["Test Studio"],
        cover_url="",  # no artwork: the seeder must not need the network
        banner_url="",
        cover_colour="",
        site_url="",
        recommendations=[],
    )


CORPUS = [
    title(1, name="Alpha", genres=["Action", "Comedy"], tags=[("Shounen", 90)], popularity=900),
    title(2, name="Beta", genres=["Drama"], tags=[("Seinen", 75), ("Mecha", 30)], popularity=800),
    title(3, name="Gamma", genres=["Horror"], year=None, popularity=700),
    title(4, name="Delta", genres=["Romance"], description="", popularity=600),
    title(5, name="Epsilon", genres=["Sports"], episodes=1, popularity=500),
]


class SeedingTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.directory = tempfile.TemporaryDirectory()
        cls.corpus_path = Path(cls.directory.name) / "corpus.json.gz"
        write_corpus(CORPUS, cls.corpus_path)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()
        super().tearDownClass()

    def seed(self, **kwargs):
        call_command(
            "seed_catalogue",
            corpus=str(self.corpus_path),
            no_posters=True,
            verbosity=0,
            **kwargs,
        )


class GenreSeedingTests(SeedingTestCase):
    def test_every_genre_is_created_even_with_nothing_in_it(self):
        """An empty genre is a shelf waiting for stock, not a bug."""
        self.seed(genres_only=True)
        self.assertEqual(Genre.objects.count(), len(GENRES))
        empty = Genre.objects.filter(animes__isnull=True).count()
        self.assertEqual(empty, len(GENRES))

    def test_genres_carry_a_description(self):
        self.seed(genres_only=True)
        self.assertFalse(Genre.objects.filter(description="").exists())

    def test_seeding_twice_creates_no_duplicates(self):
        self.seed(genres_only=True)
        self.seed(genres_only=True)
        self.assertEqual(Genre.objects.count(), len(GENRES))

    def test_an_existing_genre_keeps_its_own_description(self):
        Genre.objects.create(name="Action", description="Ours, edited by hand.")
        self.seed(genres_only=True)
        self.assertEqual(
            Genre.objects.get(name="Action").description, "Ours, edited by hand."
        )


class TitleSeedingTests(SeedingTestCase):
    def test_titles_are_created_with_their_anilist_id(self):
        self.seed()
        alpha = Anime.objects.get(name="Alpha")
        self.assertEqual(alpha.anilist_id, 1)
        self.assertEqual(alpha.release_year, 2015)
        self.assertEqual(alpha.studio, "Test Studio")

    def test_a_title_with_no_release_year_is_skipped(self):
        """The model requires a year, and a made-up one would be worse."""
        self.seed()
        self.assertFalse(Anime.objects.filter(name="Gamma").exists())

    def test_a_title_with_no_synopsis_is_skipped(self):
        self.seed()
        self.assertFalse(Anime.objects.filter(name="Delta").exists())

    def test_anilist_genres_are_applied(self):
        self.seed()
        names = set(Anime.objects.get(name="Alpha").genres.values_list("name", flat=True))
        self.assertIn("Action", names)
        self.assertIn("Comedy", names)

    def test_a_strongly_voted_tag_becomes_a_genre(self):
        """AniList files Shounen as a tag; people browse by it as a genre."""
        self.seed()
        names = set(Anime.objects.get(name="Alpha").genres.values_list("name", flat=True))
        self.assertIn("Shounen", names)

    def test_a_weakly_voted_tag_does_not(self):
        self.seed()
        names = set(Anime.objects.get(name="Beta").genres.values_list("name", flat=True))
        self.assertIn("Seinen", names)
        self.assertNotIn("Mecha", names)

    def test_episode_stubs_are_capped(self):
        self.seed()
        self.assertEqual(Episode.objects.filter(anime__name="Alpha").count(), 3)

    def test_a_one_episode_title_gets_one_stub(self):
        self.seed()
        self.assertEqual(Episode.objects.filter(anime__name="Epsilon").count(), 1)

    def test_the_count_is_respected(self):
        self.seed(count=1)
        self.assertEqual(Anime.objects.count(), 1)

    def test_the_most_popular_titles_come_first(self):
        self.seed(count=1)
        self.assertEqual(Anime.objects.get().name, "Alpha")

    def test_seeding_twice_adds_nothing_the_second_time(self):
        self.seed()
        first = Anime.objects.count()
        self.seed()
        self.assertEqual(Anime.objects.count(), first)

    def test_a_title_we_already_have_by_name_is_not_duplicated(self):
        """The legacy rows arrived without an AniList id; match on name too."""
        Anime.objects.create(
            name="Alpha", studio="Someone else", release_year=2015, synopsis="Ours."
        )
        self.seed()
        self.assertEqual(Anime.objects.filter(name="Alpha").count(), 1)
        self.assertEqual(Anime.objects.get(name="Alpha").studio, "Someone else")

    @override_settings(RECOMMENDER_CORPUS_PATH=Path("/nonexistent/corpus.json.gz"))
    def test_a_missing_corpus_is_an_explained_error(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError) as caught:
            call_command("seed_catalogue", no_posters=True, verbosity=0)
        self.assertIn("harvest_corpus", str(caught.exception))
