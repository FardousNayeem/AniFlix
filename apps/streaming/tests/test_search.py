"""Instant-search endpoint."""

from django.test import TestCase
from django.urls import reverse

from apps.streaming import selectors

from .factories import make_anime, make_genre, make_user


class SuggestionSelectorTests(TestCase):
    def setUp(self):
        self.action = make_genre("Action")
        self.gintama = make_anime(name="Gintama", studio="Sunrise", release_year=2006)
        self.gintama.genres.add(self.action)
        make_anime(name="Terror in Resonance", studio="MAPPA", release_year=2014)

    def test_a_single_character_returns_nothing(self):
        """Guards against a one-letter query asking for the whole catalogue."""
        self.assertEqual(selectors.search_suggestions(term="g"), [])
        self.assertEqual(selectors.search_suggestions(term=""), [])

    def test_a_title_match_is_returned(self):
        results = selectors.search_suggestions(term="gin")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Gintama")
        self.assertEqual(results[0]["url"], self.gintama.get_absolute_url())

    def test_studio_and_genre_also_match(self):
        self.assertEqual(selectors.search_suggestions(term="sunrise")[0]["name"], "Gintama")
        self.assertEqual(selectors.search_suggestions(term="action")[0]["name"], "Gintama")

    def test_results_are_capped(self):
        for index in range(12):
            make_anime(name=f"Filler Saga {index}")
        self.assertEqual(len(selectors.search_suggestions(term="Filler", limit=6)), 6)

    def test_a_title_in_two_matching_genres_is_not_returned_twice(self):
        self.gintama.genres.add(make_genre("Comedy"))
        results = selectors.search_suggestions(term="Gintama")
        self.assertEqual(len(results), 1)


class SuggestionViewTests(TestCase):
    def setUp(self):
        self.url = reverse("streaming:search-suggest")
        make_anime(name="Gintama")

    def test_the_endpoint_is_public(self):
        response = self.client.get(self.url, {"q": "gin"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["name"], "Gintama")

    def test_a_missing_query_returns_an_empty_list_rather_than_erroring(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])
