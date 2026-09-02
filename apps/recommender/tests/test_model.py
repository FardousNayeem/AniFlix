"""The scoring rules, tested against a model whose geometry we chose."""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from apps.recommender import model as taste
from apps.recommender.model import ModelUnavailable, Query, load_model, reset_cache, score

from .factories import AXES, IDS, write_model


class ModelLoadingTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "taste_model.json.gz"
        reset_cache()
        self.addCleanup(reset_cache)

    def test_a_missing_artifact_is_an_explained_failure_not_a_crash(self):
        with self.assertRaises(ModelUnavailable) as caught:
            load_model(self.path)
        self.assertIn("train_recommender", str(caught.exception))

    def test_the_model_is_read_once_and_cached(self):
        write_model(self.path)
        first = load_model(self.path)
        second = load_model(self.path)
        self.assertIs(first, second)

    def test_a_rewritten_artifact_is_picked_up_without_a_restart(self):
        """A deploy replaces the file under a running process."""
        from .factories import default_titles

        write_model(self.path)
        first = load_model(self.path)
        self.assertEqual(len(first.items), 5)

        write_model(self.path, titles=default_titles()[:3])
        second = load_model(self.path)
        self.assertIsNot(first, second)
        self.assertEqual(len(second.items), 3)

    def test_a_truncated_artifact_is_rejected(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"not gzip")
        with self.assertRaises(ModelUnavailable):
            load_model(self.path)


class ScoringTests(SimpleTestCase):
    """Ranking behaviour, with the taste space pinned to four axes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / "taste_model.json.gz"
        write_model(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()
        super().tearDownClass()

    def setUp(self):
        reset_cache()
        self.addCleanup(reset_cache)
        self.model = load_model(self.path)

    def ranked(self, query):
        return [scored.item.name for scored in score(self.model, query)]

    def test_a_dark_query_ranks_the_dark_title_first(self):
        query = Query()
        query.add_terms({"genre:psychological": 1.0})
        self.assertEqual(self.ranked(query)[0], "Grim Thing")

    def test_a_funny_query_ranks_the_funny_title_first(self):
        query = Query()
        query.add_terms({"genre:comedy": 1.0})
        self.assertEqual(self.ranked(query)[0], "Funny Thing")

    def test_an_orthogonal_title_never_scores_negative(self):
        """Cosine can go below zero; a percentage shown to a viewer cannot.

        It is not exactly zero either: every title carries a small popularity
        prior. What has to hold is that a title with nothing in common ranks
        far below one that matches.
        """
        query = Query()
        query.add_terms({"genre:comedy": 1.0})
        scores = {scored.item.name: scored.fit for scored in score(self.model, query)}
        self.assertGreaterEqual(scores["Grim Thing"], 0.0)
        self.assertLess(scores["Grim Thing"], scores["Funny Thing"] / 2)

    def test_a_stated_length_reorders_equally_liked_titles(self):
        """Both are funny-adjacent; only one is short."""
        query = Query()
        query.add_terms({"genre:comedy": 1.0})
        without = self.ranked(query)

        constrained = Query()
        constrained.add_terms({"genre:comedy": 1.0})
        constrained.facets["length"] = "long"
        self.assertEqual(without[0], "Funny Thing")
        self.assertEqual(self.ranked(constrained)[0], "Funny Thing")

        short = Query()
        short.add_terms({"genre:action": 1.0})
        short.facets["length"] = "short"
        self.assertEqual(self.ranked(short)[0], "Punch Thing")

    def test_an_avoided_term_reorders_two_otherwise_equal_titles(self):
        """Both are thrillers at the same point; only one is psychological."""
        query = Query()
        query.add_terms({"genre:thriller": 1.0})
        fits = {scored.item.name: scored.fit for scored in score(self.model, query)}
        self.assertAlmostEqual(fits["Grim Thing"], fits["Bleak Thing"], places=6)

        avoided = Query()
        avoided.add_terms({"genre:thriller": 1.0})
        avoided.add_avoid({"genre:psychological": 1.0})
        ranked = score(self.model, avoided)
        order = [scored.item.name for scored in ranked]
        self.assertLess(order.index("Bleak Thing"), order.index("Grim Thing"))

        demoted = next(item for item in ranked if item.item.name == "Grim Thing")
        self.assertEqual(demoted.avoided_terms, ("genre:psychological",))
        self.assertLess(demoted.fit, fits["Grim Thing"])

    def test_something_already_seen_is_demoted_but_still_offered(self):
        query = Query()
        query.add_terms({"genre:psychological": 1.0})
        self.assertEqual(self.ranked(query)[0], "Grim Thing")

        seen = Query()
        seen.add_terms({"genre:psychological": 1.0})
        seen.seen_ids = frozenset({IDS["grim"]})
        ranked = score(self.model, seen)
        demoted = next(item for item in ranked if item.item.name == "Grim Thing")
        self.assertTrue(demoted.already_seen)
        self.assertGreater(demoted.fit, 0.0)  # offered, not hidden
        self.assertLess(demoted.fit, score(self.model, query)[0].fit)

    def test_fit_never_leaves_the_zero_to_one_range(self):
        query = Query()
        query.add_terms({"genre:thriller": 1.0, "genre:comedy": 1.0})
        query.add_avoid({"genre:psychological": 1.0, "tag:parody": 1.0})
        query.facets["length"] = "short"
        for scored in score(self.model, query):
            self.assertGreaterEqual(scored.fit, 0.0)
            self.assertLessEqual(scored.fit, 1.0)

    def test_an_empty_query_does_not_divide_by_zero(self):
        self.assertEqual(len(score(self.model, Query())), 5)


class FreeTextTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / "taste_model.json.gz"
        write_model(
            cls.path,
            references={
                "grim reference": {"label": "Grim Reference", "vector": AXES["dark"]},
                "reference": {"label": "Reference", "vector": AXES["funny"]},
            },
        )

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()
        super().tearDownClass()

    def setUp(self):
        reset_cache()
        self.addCleanup(reset_cache)
        self.model = load_model(self.path)

    def test_typed_words_reach_the_same_terms_as_the_taps(self):
        terms = self.model.text_terms("something dark and psychological")
        self.assertIn("genre:psychological", terms)
        self.assertIn("word:dark", terms)

    def test_a_plural_still_finds_the_singular_term(self):
        """Training never stemmed, so the query side folds instead."""
        self.assertIn("word:pirate", self.model.text_terms("pirates and treasure"))

    def test_unknown_words_are_ignored_rather_than_guessed_at(self):
        self.assertEqual(self.model.text_terms("zzzzqx wibblefrotz"), {})

    def test_a_named_title_is_recognised(self):
        found = self.model.find_references("something like Grim Reference")
        self.assertEqual([reference.label for reference in found], ["Grim Reference"])

    def test_a_longer_title_is_not_also_matched_as_the_shorter_one_inside_it(self):
        found = self.model.find_references("something like Grim Reference")
        self.assertNotIn("Reference", [reference.label for reference in found])

    # The two dark titles sit at the same point, so a dark query ties between
    # them. These assert the neighbourhood, which is what a named title picks.
    DARK = {"Grim Thing", "Bleak Thing"}

    def test_a_named_title_steers_the_result(self):
        query = Query()
        query.add_free_text("something like Grim Reference", self.model)
        self.assertIn(score(self.model, query)[0].item.name, self.DARK)

    def test_a_named_title_beats_the_words_around_it(self):
        """"like Grim Reference but comedy" must not become a comedy query."""
        query = Query()
        query.add_free_text("like Grim Reference but comedy", self.model)
        self.assertIn(score(self.model, query)[0].item.name, self.DARK)

    def test_an_everyday_word_reaches_the_genre_it_means(self):
        """Nobody types "genre:comedy". They type "funny"."""
        wanted, _ = self.model.read_text("something funny")
        self.assertIn("genre:comedy", wanted)

    def test_a_phrase_shorter_than_the_tokeniser_still_matches(self):
        """"cheer me up" survives even though "me" and "up" are dropped."""
        wanted, _ = self.model.read_text("cheer me up please")
        self.assertIn("genre:comedy", wanted)

    def test_a_negated_clause_becomes_an_avoid_not_a_want(self):
        wanted, avoided = self.model.read_text("anything but comedy")
        self.assertIn("genre:comedy", avoided)
        self.assertNotIn("genre:comedy", wanted)

    def test_one_sentence_can_want_and_refuse_at_once(self):
        wanted, avoided = self.model.read_text("something funny, not too long")
        self.assertIn("genre:comedy", wanted)
        self.assertIn("length:long", avoided)
        self.assertNotIn("genre:comedy", avoided)

    def test_but_only_negates_in_anything_but(self):
        """"dark but funny" wants both; "anything but funny" refuses one."""
        wanted, avoided = self.model.read_text("something psychological but funny")
        self.assertIn("genre:comedy", wanted)
        self.assertEqual(avoided, {})

    def test_a_refusal_does_not_drag_loose_words_in_with_it(self):
        """Only real terms are avoidable; stray nouns near a "not" are noise."""
        _, avoided = self.model.read_text("not a comedy about pirates")
        self.assertIn("genre:comedy", avoided)
        self.assertNotIn("word:pirate", avoided)

    def test_a_misspelt_genre_is_still_understood(self):
        wanted, _ = self.model.read_text("somthing psycological")
        self.assertIn("genre:psychological", wanted)

    def test_an_unrelated_word_is_not_corrected_into_a_genre(self):
        """The correction must not invent an answer nobody asked for."""
        for token in ("weather", "tomorrow", "rice"):
            self.assertIsNone(self.model._correct(token))

    def test_a_refusal_alone_still_ranks_the_catalogue(self):
        query = Query()
        query.add_free_text("anything but comedy", self.model)
        ranked = score(self.model, query)
        self.assertTrue(ranked)
        funny = next(item for item in ranked if item.item.name == "Funny Thing")
        self.assertIn("genre:comedy", funny.avoided_terms)

    def test_words_alone_still_steer_when_no_title_is_named(self):
        query = Query()
        query.add_free_text("comedy", self.model)
        self.assertEqual(score(self.model, query)[0].item.name, "Funny Thing")


@override_settings()
class ProjectionTests(SimpleTestCase):
    """The maths the runtime repeats from training."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / "taste_model.json.gz"
        write_model(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()
        super().tearDownClass()

    def setUp(self):
        reset_cache()
        self.addCleanup(reset_cache)
        self.model = load_model(self.path)

    def test_a_projected_query_is_unit_length(self):
        vector = self.model.project({"genre:thriller": 1.0, "genre:comedy": 0.5})
        length = sum(value * value for value in vector) ** 0.5
        self.assertAlmostEqual(length, 1.0, places=6)

    def test_a_query_of_only_unknown_terms_projects_to_the_origin(self):
        self.assertEqual(
            self.model.project({"genre:nonsense": 1.0}), [0.0] * self.model.dimensions
        )

    def test_weighting_two_terms_moves_the_query_between_them(self):
        mostly_dark = self.model.project({"genre:thriller": 1.0, "genre:comedy": 0.1})
        mostly_funny = self.model.project({"genre:thriller": 0.1, "genre:comedy": 1.0})
        dark_axis = AXES["dark"]
        self.assertGreater(
            taste.cosine(mostly_dark, dark_axis), taste.cosine(mostly_funny, dark_axis)
        )
