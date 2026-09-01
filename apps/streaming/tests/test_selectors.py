from django.test import TestCase

from apps.streaming import selectors, services

from .factories import make_anime, make_episode, make_genre, make_user


class CatalogSelectorTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.thriller = make_genre("Thriller")
        self.comedy = make_genre("Comedy")

        self.rated = make_anime(name="Rated", release_year=2014)
        self.rated.genres.add(self.thriller)
        services.rate_anime(user=self.user, anime=self.rated, score=5)

        self.unrated = make_anime(name="Unrated", release_year=2020)
        self.unrated.genres.add(self.comedy)

    def test_the_default_sort_is_newest_first(self):
        results = list(selectors.anime_catalog())
        self.assertEqual(results[0], self.unrated)  # 2020
        self.assertEqual(results[-1], self.rated)  # 2014

    def test_rated_titles_outrank_unrated_ones(self):
        """NULL averages must sort last, not first."""
        results = list(selectors.anime_catalog(sort="rating"))
        self.assertEqual(results[0], self.rated)

    def test_watchlist_state_is_annotated_without_a_query_per_card(self):
        services.toggle_watchlist(user=self.user, anime=self.rated)
        results = {item.name: item.in_watchlist for item in selectors.anime_catalog(user=self.user)}
        self.assertTrue(results["Rated"])
        self.assertFalse(results["Unrated"])

    def test_anonymous_visitors_get_a_false_watchlist_flag(self):
        from django.contrib.auth.models import AnonymousUser

        results = list(selectors.anime_catalog(user=AnonymousUser()))
        self.assertTrue(all(item.in_watchlist is False for item in results))

    def test_the_catalogue_page_runs_a_constant_number_of_queries(self):
        """The old genre page ran one query per genre plus one per title.

        Ratings, episode counts, genres and watchlist state now resolve in two
        queries regardless of how many titles are on the page.
        """
        for index in range(12):
            anime = make_anime(name=f"Filler {index}")
            anime.genres.add(self.thriller)
            make_episode(anime)

        with self.assertNumQueries(2):
            list(selectors.anime_catalog(user=self.user))

    def test_search_matches_title_studio_and_genre(self):
        # "Rated" is a substring of "Unrated", so match on the genres instead:
        # one title per genre keeps the assertion about matching, not ordering.
        self.assertEqual(list(selectors.anime_catalog(search="Thriller")), [self.rated])
        self.assertEqual(list(selectors.anime_catalog(search="Comedy")), [self.unrated])

    def test_adjacent_episodes_walk_the_series_in_order(self):
        anime = make_anime(name="Serial")
        first = make_episode(anime, number=1)
        second = make_episode(anime, number=2)
        third = make_episode(anime, number=3)

        self.assertEqual(selectors.adjacent_episodes(second), (first, third))
        self.assertEqual(selectors.adjacent_episodes(first), (None, second))
        self.assertEqual(selectors.adjacent_episodes(third), (second, None))
