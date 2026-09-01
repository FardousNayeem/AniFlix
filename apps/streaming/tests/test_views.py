from django.test import TestCase
from django.urls import reverse

from apps.streaming.models import Rating, WatchlistEntry

from .factories import make_anime, make_episode, make_genre, make_user


class CatalogViewTests(TestCase):
    def setUp(self):
        self.genre = make_genre("Thriller")
        self.anime = make_anime()
        self.anime.genres.add(self.genre)
        make_anime(name="Gintama", studio="Sunrise", release_year=2006)

    def test_home_lists_titles_for_anonymous_visitors(self):
        response = self.client.get(reverse("streaming:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Terror in Resonance")

    def test_search_filters_the_catalogue(self):
        response = self.client.get(reverse("streaming:browse"), {"q": "Gintama"})
        self.assertContains(response, "Gintama")
        self.assertNotContains(response, "Terror in Resonance")

    def test_sorting_keeps_the_active_search_term(self):
        """Selecting a sort used to reset the query and reload the whole catalogue."""
        response = self.client.get(reverse("streaming:browse"), {"q": "Gintama", "sort": "az"})
        self.assertEqual(response.context["search"], "Gintama")
        self.assertEqual(response.context["sort"], "az")

    def test_an_unknown_sort_value_falls_back_instead_of_erroring(self):
        response = self.client.get(reverse("streaming:browse"), {"sort": "'; DROP TABLE"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "newest")

    def test_the_catalogue_defaults_to_newest_first(self):
        response = self.client.get(reverse("streaming:browse"))
        self.assertEqual(response.context["sort"], "newest")

    def test_a_page_number_past_the_end_shows_the_last_page(self):
        response = self.client.get(reverse("streaming:browse"), {"page": "9999"})
        self.assertEqual(response.status_code, 200)

    def test_a_missing_title_returns_404_not_500(self):
        response = self.client.get("/anime/no-such-title/")
        self.assertEqual(response.status_code, 404)

    def test_genre_page_lists_only_that_genre(self):
        response = self.client.get(self.genre.get_absolute_url())
        self.assertContains(response, "Terror in Resonance")
        self.assertNotContains(response, "Gintama")


class WatchlistViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.anime = make_anime()
        self.url = reverse("streaming:anime-watchlist-toggle", args=[self.anime.slug])

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.headers["Location"])

    def test_get_is_rejected(self):
        """A GET used to toggle state, so a prefetch could unsave a title."""
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_ajax_toggle_returns_the_new_state(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["in_watchlist"])
        self.assertEqual(WatchlistEntry.objects.count(), 1)


class RatingViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.anime = make_anime()
        self.url = reverse("streaming:anime-rate", args=[self.anime.slug])
        self.client.force_login(self.user)

    def test_a_valid_score_is_saved(self):
        self.client.post(self.url, {"score": 4})
        self.assertEqual(Rating.objects.get(user=self.user, anime=self.anime).score, 4)

    def test_an_invalid_score_is_refused_without_creating_a_row(self):
        self.client.post(self.url, {"score": 11})
        self.assertFalse(Rating.objects.exists())


class EpisodeViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.anime = make_anime()
        self.episode = make_episode(self.anime, number=1)
        make_episode(self.anime, number=2)

    def test_episode_page_renders_and_links_the_next_episode(self):
        response = self.client.get(self.episode.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["next_episode"].number, 2)
        self.assertIsNone(response.context["previous_episode"])

    def test_an_unknown_episode_number_returns_404(self):
        response = self.client.get(f"/anime/{self.anime.slug}/episode/99/")
        self.assertEqual(response.status_code, 404)

    def test_posting_a_comment_requires_sign_in(self):
        response = self.client.post(self.episode.get_absolute_url(), {"body": "Nice"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.headers["Location"])

    def test_a_signed_in_user_can_comment(self):
        self.client.force_login(self.user)
        response = self.client.post(self.episode.get_absolute_url(), {"body": "That ending though."})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.episode.comments.count(), 1)

    def test_an_empty_comment_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(self.episode.get_absolute_url(), {"body": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.episode.comments.count(), 0)


class AsyncRatingTests(TestCase):
    """The star control saves without reloading the page."""

    def setUp(self):
        self.user = make_user()
        self.anime = make_anime()
        self.url = reverse("streaming:anime-rate", args=[self.anime.slug])
        self.client.force_login(self.user)

    def test_an_ajax_rating_returns_the_recalculated_average(self):
        response = self.client.post(self.url, {"score": 4}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["average"], "4.0")
        self.assertEqual(payload["count_label"], "from 1 rating")

    def test_the_average_reflects_every_voter(self):
        other = make_user("other@example.com")
        self.client.post(self.url, {"score": 5}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.client.force_login(other)
        response = self.client.post(self.url, {"score": 3}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.json()["average"], "4.0")
        self.assertEqual(response.json()["count_label"], "from 2 ratings")

    def test_an_invalid_ajax_rating_returns_400_and_a_reason(self):
        response = self.client.post(self.url, {"score": 9}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertIn("between 1 and 5", response.json()["message"])

    def test_a_plain_form_post_still_redirects(self):
        response = self.client.post(self.url, {"score": 4})
        self.assertRedirects(response, self.anime.get_absolute_url())
