"""The two endpoints, and the rules that stand behind them."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.recommender import services
from apps.recommender.model import load_model, reset_cache
from apps.streaming.models import Anime, Genre, Rating, WatchlistEntry
from apps.streaming.tests.factories import make_user

from .factories import IDS, write_model


# Deliberately not every fixture title: "Calm Thing" is one the model knows
# and the site does not stock, which is the case the widget has to label.
STOCKED = ("grim", "bleak", "funny", "punch")


def make_catalogue(stocked=STOCKED):
    """Catalogue rows linked to the fixture model by AniList id."""
    thriller = Genre.objects.create(name="Thriller")
    names = {
        "grim": ("Grim Thing", 2014),
        "bleak": ("Bleak Thing", 2014),
        "funny": ("Funny Thing", 2006),
        "punch": ("Punch Thing", 1999),
        "calm": ("Calm Thing", 2018),
    }
    for key in stocked:
        name, year = names[key]
        anime = Anime.objects.create(
            name=name,
            studio="Test",
            release_year=year,
            synopsis="A test title.",
            anilist_id=IDS[key],
        )
        anime.genres.add(thriller)


class RecommenderTestCase(TestCase):
    """Every test here runs against the hand-built model, never the real one."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.directory = tempfile.TemporaryDirectory()
        cls.model_path = Path(cls.directory.name) / "taste_model.json.gz"
        write_model(cls.model_path)
        cls.override = override_settings(RECOMMENDER_MODEL_PATH=cls.model_path)
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.override.disable()
        cls.directory.cleanup()
        super().tearDownClass()

    def setUp(self):
        reset_cache()
        self.addCleanup(reset_cache)
        make_catalogue()

    def ask(self, **payload):
        return self.client.post(
            reverse("recommender:ask"),
            data=json.dumps(payload),
            content_type="application/json",
        )


class QuestionsEndpointTests(RecommenderTestCase):
    def test_the_questions_are_served_with_their_options(self):
        response = self.client.get(reverse("recommender:questions"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["available"])
        self.assertTrue(payload["questions"])
        first = payload["questions"][0]
        self.assertIn("prompt", first)
        self.assertTrue(first["options"])

    def test_the_endpoint_rejects_post(self):
        response = self.client.post(reverse("recommender:questions"))
        self.assertEqual(response.status_code, 405)

    def test_fetching_the_questions_plants_the_csrf_cookie(self):
        """Otherwise the first ask from a formless page is rejected."""
        response = self.client.get(reverse("recommender:questions"))
        self.assertIn("csrftoken", response.cookies)

    @override_settings(RECOMMENDER_MODEL_PATH=Path("/nonexistent/taste_model.json.gz"))
    def test_an_untrained_server_says_so_instead_of_erroring(self):
        """The widget hides itself on this, rather than offering a dead button."""
        reset_cache()
        response = self.client.get(reverse("recommender:questions"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["available"])
        self.assertEqual(response.json()["questions"], [])


class AskEndpointTests(RecommenderTestCase):
    def test_an_anonymous_visitor_gets_recommendations(self):
        response = self.ask(answers={"mood": ["dark"]})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["results"])

    def test_a_result_carries_what_the_card_needs(self):
        response = self.ask(answers={"mood": ["dark"]})
        first = response.json()["results"][0]
        for key in ("id", "name", "url", "available", "match", "reasons", "poster"):
            self.assertIn(key, first)
        self.assertGreaterEqual(first["match"], 0)
        self.assertLessEqual(first["match"], 100)

    def test_the_mood_answer_changes_the_answer(self):
        dark = self.ask(answers={"mood": ["dark"]}).json()["results"][0]["name"]
        funny = self.ask(answers={"mood": ["funny"]}).json()["results"][0]["name"]
        self.assertNotEqual(dark, funny)
        self.assertEqual(funny, "Funny Thing")

    def test_the_endpoint_rejects_get(self):
        response = self.client.get(reverse("recommender:ask"))
        self.assertEqual(response.status_code, 405)

    def test_a_malformed_body_is_a_400_not_a_500(self):
        response = self.client.post(
            reverse("recommender:ask"), data="{not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_an_unknown_question_is_rejected(self):
        response = self.ask(answers={"favourite-colour": ["blue"]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("favourite-colour", response.json()["message"])

    def test_an_unknown_option_is_rejected(self):
        response = self.ask(answers={"mood": ["sepia"]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("sepia", response.json()["message"])

    def test_too_many_choices_are_rejected(self):
        """'mood' takes two; three would quietly become a different query."""
        response = self.ask(answers={"mood": ["dark", "funny", "action"]})
        self.assertEqual(response.status_code, 400)

    def test_an_empty_ask_is_rejected(self):
        response = self.ask(answers={})
        self.assertEqual(response.status_code, 400)

    def test_answers_must_be_lists(self):
        response = self.ask(answers={"mood": "dark"})
        self.assertEqual(response.status_code, 400)

    def test_an_oversized_body_is_refused(self):
        response = self.ask(answers={"mood": ["dark"]}, text="x" * 9000)
        self.assertEqual(response.status_code, 413)

    def test_free_text_alone_is_enough(self):
        response = self.ask(answers={}, text="something dark and psychological")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["name"], "Grim Thing")

    def test_free_text_of_pure_nonsense_says_so(self):
        response = self.ask(answers={}, text="zzzzqx wibblefrotz")
        self.assertEqual(response.status_code, 400)
        self.assertIn("model knows", response.json()["message"])

    @override_settings(RECOMMENDER_MODEL_PATH=Path("/nonexistent/taste_model.json.gz"))
    def test_an_untrained_server_returns_503_not_a_crash(self):
        reset_cache()
        response = self.ask(answers={"mood": ["dark"]})
        self.assertEqual(response.status_code, 503)


class PersonalisationTests(RecommenderTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("fan@example.com")

    def test_a_title_already_on_the_watchlist_is_marked(self):
        WatchlistEntry.objects.create(
            user=self.user, anime=Anime.objects.get(anilist_id=IDS["grim"])
        )
        self.client.force_login(self.user)
        results = self.ask(answers={"mood": ["dark"]}).json()["results"]
        marked = {item["name"]: item["alreadySeen"] for item in results}
        self.assertTrue(marked.get("Grim Thing", False) or "Grim Thing" not in marked)

    def test_a_watchlisted_title_loses_to_an_equal_one_that_is_new(self):
        """Both dark titles tie; the one they have already saved should yield."""
        WatchlistEntry.objects.create(
            user=self.user, anime=Anime.objects.get(anilist_id=IDS["grim"])
        )
        self.client.force_login(self.user)
        results = self.ask(answers={"mood": ["dark"]}).json()["results"]
        order = [item["name"] for item in results]
        self.assertIn("Bleak Thing", order)
        if "Grim Thing" in order:
            self.assertLess(order.index("Bleak Thing"), order.index("Grim Thing"))

    def test_an_anonymous_visitor_is_not_personalised(self):
        WatchlistEntry.objects.create(
            user=self.user, anime=Anime.objects.get(anilist_id=IDS["grim"])
        )
        results = self.ask(answers={"mood": ["dark"]}).json()["results"]
        self.assertFalse(any(item["alreadySeen"] for item in results))

    def test_a_rating_counts_as_seen(self):
        Rating.objects.create(
            user=self.user, anime=Anime.objects.get(anilist_id=IDS["funny"]), score=5
        )
        self.client.force_login(self.user)
        results = self.ask(answers={"mood": ["funny"]}).json()["results"]
        seen = {item["name"]: item["alreadySeen"] for item in results}
        self.assertTrue(seen.get("Funny Thing"))


class ServiceRuleTests(RecommenderTestCase):
    """Rules worth stating without a request in the way."""

    def test_a_title_we_do_not_stock_is_offered_and_labelled(self):
        """The model knows more titles than the site carries, and says so."""
        results = services.recommend(answers={"mood": ["calm"]}, limit=5)
        calm = next(item for item in results if item.name == "Calm Thing")
        self.assertFalse(calm.available)
        self.assertIsNone(calm.url)
        # Still worth showing, so it keeps the artwork the model shipped.
        self.assertTrue(calm.poster_url)

    def test_a_title_we_stock_links_to_its_page(self):
        results = services.recommend(answers={"mood": ["dark"]}, limit=5)
        grim = next(item for item in results if item.name == "Grim Thing")
        self.assertTrue(grim.available)
        self.assertTrue(grim.url.startswith("/anime/"))

    def test_deleting_a_title_makes_it_unavailable_not_absent(self):
        """The artifact outlives a deletion until somebody retrains."""
        Anime.objects.filter(anilist_id=IDS["grim"]).delete()
        results = services.recommend(answers={"mood": ["dark"]}, limit=5)
        grim = next(item for item in results if item.name == "Grim Thing")
        self.assertFalse(grim.available)

    def test_the_result_count_is_configurable(self):
        with override_settings(RECOMMENDER_RESULT_COUNT=2):
            self.assertEqual(len(services.recommend(answers={"mood": ["dark"]})), 2)

    def test_an_optional_question_may_be_skipped(self):
        query = services.build_query(answers={"mood": ["dark"], "avoid": []})
        self.assertEqual(query.avoid, {})

    def test_a_required_question_may_not_be_answered_empty(self):
        with self.assertRaises(ValidationError):
            services.build_query(answers={"mood": []})

    def test_free_text_is_truncated_rather_than_refused(self):
        """A pasted essay is cut down, not rejected."""
        essay = "dark " * 200
        query = services.build_query(
            answers={"mood": ["dark"]}, free_text=essay, model=load_model()
        )
        self.assertLess(len(query.free_text), len(essay))
        self.assertLessEqual(len(query.free_text), services.MAX_FREE_TEXT)
        self.assertIn("word:dark", query.terms)
