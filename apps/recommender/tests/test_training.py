"""The training maths.

Skipped where numpy is absent, which is the normal state of a deploy: these
cover a build step, not the request path. Run them with
``pip install -r requirements-ml.txt``.

Each test states a property the algorithm must have, rather than a number it
happened to produce, so retuning a hyperparameter does not turn the suite red
for no reason.
"""

from __future__ import annotations

import unittest

from django.test import SimpleTestCase

try:  # pragma: no cover - the import is the point
    import numpy as np

    from apps.recommender.ml import train as training
    from apps.recommender.ml.harvest import HarvestedTitle

    HAS_NUMPY = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_NUMPY = False


def title(anilist_id, *, name="Thing", genres=(), tags=(), description="", year=2015,
          episodes=12, recommendations=()):
    return HarvestedTitle(
        anilist_id=anilist_id,
        romaji=name,
        english=name,
        genres=list(genres),
        tags=list(tags),
        description=description,
        score=80,
        popularity=1000,
        episodes=episodes,
        duration=24,
        year=year,
        format="TV",
        studios=["Studio"],
        cover_url=f"https://example.test/{anilist_id}.jpg",
        banner_url="",
        cover_colour="#112233",
        site_url=f"https://anilist.co/anime/{anilist_id}",
        recommendations=list(recommendations),
    )


@unittest.skipUnless(HAS_NUMPY, "training needs numpy (requirements-ml.txt)")
class FeatureTests(SimpleTestCase):
    def test_a_genre_outweighs_the_same_word_in_a_synopsis(self):
        features = training.title_features(
            title(1, genres=["Action"], description="an action packed action story")
        )
        self.assertGreater(features["genre:action"], features["word:action"])

    def test_a_weakly_voted_tag_is_ignored(self):
        """A 20%-rank tag is a handful of voters, not a description."""
        features = training.title_features(title(1, tags=[("Mecha", 20), ("Gore", 90)]))
        self.assertNotIn("tag:mecha", features)
        self.assertIn("tag:gore", features)

    def test_a_strong_tag_counts_for_more_than_a_borderline_one(self):
        features = training.title_features(title(1, tags=[("Gore", 95), ("Magic", 45)]))
        self.assertGreater(features["tag:gore"], features["tag:magic"])

    def test_length_and_era_become_facets(self):
        features = training.title_features(title(1, year=1998, episodes=8))
        self.assertIn("era:classic", features)
        self.assertIn("length:short", features)

    def test_title_words_survive_the_synopsis_filter(self):
        """"Death Note" must not lose "note" to description boilerplate."""
        features = training.title_features(title(1, name="Death Note"))
        self.assertIn("word:note", features)

    def test_synopsis_boilerplate_is_still_filtered(self):
        features = training.title_features(
            title(1, name="Thing", description="Based on the light novel series.")
        )
        self.assertNotIn("word:novel", features)


@unittest.skipUnless(HAS_NUMPY, "training needs numpy (requirements-ml.txt)")
class GraphTests(SimpleTestCase):
    def test_the_graph_is_symmetric(self):
        """A recommends B means B is near A, whichever way the vote was cast."""
        titles = [title(1, recommendations=[(2, 50)]), title(2)]
        index = {1: 0, 2: 1}
        matrix = training.build_cooccurrence(titles, index)
        self.assertEqual(matrix[0][1], matrix[1][0])
        self.assertGreater(matrix[0][1], 0)

    def test_a_rejected_suggestion_is_dropped(self):
        titles = [title(1, recommendations=[(2, -30)]), title(2)]
        matrix = training.build_cooccurrence(titles, {1: 0, 2: 1})
        self.assertEqual(matrix.sum(), 0)

    def test_votes_are_compressed_so_a_blockbuster_cannot_dominate(self):
        huge = training.build_cooccurrence(
            [title(1, recommendations=[(2, 3000)]), title(2)], {1: 0, 2: 1}
        )[0][1]
        small = training.build_cooccurrence(
            [title(1, recommendations=[(2, 30)]), title(2)], {1: 0, 2: 1}
        )[0][1]
        self.assertLess(huge / small, 3.0)

    def test_ppmi_is_never_negative(self):
        matrix = np.array([[0.0, 2.0, 0.0], [2.0, 0.0, 1.0], [0.0, 1.0, 0.0]], dtype=np.float32)
        self.assertTrue((training.ppmi(matrix) >= 0).all())


@unittest.skipUnless(HAS_NUMPY, "training needs numpy (requirements-ml.txt)")
class DecompositionTests(SimpleTestCase):
    def test_the_randomised_svd_matches_the_exact_one(self):
        """The speed-up must not cost accuracy at the ranks we use."""
        generator = np.random.default_rng(7)
        base = generator.standard_normal((120, 8)).astype(np.float32)
        matrix = (base @ base.T).astype(np.float32)  # rank 8, symmetric

        _, approx = training.randomised_svd(matrix, 8)
        exact = np.linalg.svd(matrix, compute_uv=False)[:8]
        np.testing.assert_allclose(approx, exact, rtol=0.02)

    def test_embeddings_come_back_unit_length(self):
        generator = np.random.default_rng(7)
        base = generator.standard_normal((60, 5)).astype(np.float32)
        embedding = training.embed_items(np.abs(base @ base.T).astype(np.float32), dims=5)
        np.testing.assert_allclose(np.linalg.norm(embedding, axis=1), 1.0, rtol=1e-5)

    def test_titles_recommended_together_end_up_near_each_other(self):
        """The whole point of stage one, on a graph small enough to verify."""
        # Two cliques that never reference each other.
        titles = [
            title(1, recommendations=[(2, 100), (3, 100)]),
            title(2, recommendations=[(1, 100), (3, 100)]),
            title(3, recommendations=[(1, 100), (2, 100)]),
            title(4, recommendations=[(5, 100), (6, 100)]),
            title(5, recommendations=[(4, 100), (6, 100)]),
            title(6, recommendations=[(4, 100), (5, 100)]),
        ]
        index = {t.anilist_id: i for i, t in enumerate(titles)}
        embedding = training.embed_items(
            training.ppmi(training.build_cooccurrence(titles, index)), dims=4
        )
        within = float(embedding[0] @ embedding[1])
        across = float(embedding[0] @ embedding[4])
        self.assertGreater(within, across)


@unittest.skipUnless(HAS_NUMPY, "training needs numpy (requirements-ml.txt)")
class ProjectionTests(SimpleTestCase):
    def test_the_ridge_fit_recovers_a_linear_relationship(self):
        generator = np.random.default_rng(3)
        features = generator.standard_normal((200, 12)).astype(np.float32)
        truth = generator.standard_normal((12, 4)).astype(np.float32)
        targets = features @ truth

        weights = training.fit_projection(features, targets, ridge=1e-6)
        np.testing.assert_allclose(weights, truth, atol=1e-3)

    def test_more_regularisation_shrinks_the_weights(self):
        generator = np.random.default_rng(3)
        features = generator.standard_normal((80, 20)).astype(np.float32)
        targets = generator.standard_normal((80, 4)).astype(np.float32)

        light = np.abs(training.fit_projection(features, targets, ridge=0.01)).sum()
        heavy = np.abs(training.fit_projection(features, targets, ridge=100.0)).sum()
        self.assertLess(heavy, light)

    def test_vectorised_rows_are_unit_length(self):
        rows = [training.title_features(title(1, genres=["Action"], description="a fight"))]
        vocabulary = training.build_vocabulary(rows, size=50)
        # A one-document corpus keeps every term only if the floor allows it.
        if not vocabulary.terms:
            self.skipTest("document-frequency floor removed every term")
        matrix = training.vectorise(rows, vocabulary)
        np.testing.assert_allclose(np.linalg.norm(matrix, axis=1), 1.0, rtol=1e-5)


@unittest.skipUnless(HAS_NUMPY, "training needs numpy (requirements-ml.txt)")
class MatchingTests(SimpleTestCase):
    """Catalogue rows to AniList titles."""

    def setUp(self):
        from apps.recommender.ml import catalogue

        self.catalogue = catalogue
        self.corpus = [
            title(1, name="Hunter x Hunter", year=1999),
            title(2, name="Hunter x Hunter", year=2011),
            title(3, name="One-Punch Man", year=2015),
            title(4, name="Cowboy Bebop", year=1998),
        ]

    def match(self, name, year):
        found, _ = self.catalogue.match_catalogue_title(
            name=name, year=year, corpus=self.corpus
        )
        return found

    def test_the_year_separates_a_remake_from_the_original(self):
        self.assertEqual(self.match("Hunter x Hunter", 1999).year, 1999)
        self.assertEqual(self.match("Hunter x Hunter", 2011).year, 2011)

    def test_punctuation_and_season_noise_do_not_block_a_match(self):
        self.assertEqual(self.match("One Punch Man Season One", 2015).anilist_id, 3)

    def test_an_unrelated_title_is_not_forced_onto_a_match(self):
        self.assertIsNone(self.match("Completely Different Show", 2020))
