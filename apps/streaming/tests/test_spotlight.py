"""Homepage spotlight ranking.

The spotlight answers "what is the fandom picking up right now", so it ranks by
bookmarks added inside a rolling window rather than by lifetime views.
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.streaming import selectors, services
from apps.streaming.models import WatchlistEntry

from .factories import make_anime, make_user


def save_anime(user, anime, *, days_ago: int = 0) -> WatchlistEntry:
    """Bookmark a title, optionally backdated outside the window."""
    entry = WatchlistEntry.objects.create(user=user, anime=anime)
    if days_ago:
        WatchlistEntry.objects.filter(pk=entry.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
    return entry


class SpotlightRankingTests(TestCase):
    def setUp(self):
        self.users = [make_user(f"fan{index}@example.com") for index in range(5)]
        self.popular = make_anime(name="Bookmarked This Week", view_count=1)
        self.watched = make_anime(name="Lots Of Views", view_count=9999)
        self.quiet = make_anime(name="Nobody Cares", view_count=5)

    def test_recent_bookmarks_beat_lifetime_views(self):
        """A title everyone is saving now outranks one with an old view pile."""
        for user in self.users[:3]:
            save_anime(user, self.popular)

        picks = selectors.spotlight_animes(limit=3)
        self.assertEqual(picks[0], self.popular)

    def test_bookmarks_older_than_the_window_do_not_count(self):
        for user in self.users[:4]:
            save_anime(user, self.quiet, days_ago=30)
        save_anime(self.users[0], self.popular)

        picks = selectors.spotlight_animes(limit=3)
        self.assertEqual(picks[0], self.popular)
        self.assertEqual(picks[0].recent_saves, 1)

    def test_the_window_length_is_configurable(self):
        save_anime(self.users[0], self.quiet, days_ago=20)

        self.assertEqual(selectors.spotlight_animes(limit=1, window_days=7)[0].recent_saves, 0)
        self.assertEqual(selectors.spotlight_animes(limit=1, window_days=30)[0], self.quiet)

    @override_settings(SPOTLIGHT_WINDOW_DAYS=14)
    def test_the_window_defaults_to_the_setting(self):
        save_anime(self.users[0], self.quiet, days_ago=10)
        self.assertEqual(selectors.spotlight_animes(limit=1)[0], self.quiet)

    def test_titles_are_ranked_by_how_many_people_saved_them(self):
        for user in self.users[:2]:
            save_anime(user, self.quiet)
        for user in self.users[:4]:
            save_anime(user, self.popular)

        picks = selectors.spotlight_animes(limit=2)
        self.assertEqual([anime.name for anime in picks], [self.popular.name, self.quiet.name])
        self.assertEqual(picks[0].recent_saves, 4)
        self.assertEqual(picks[1].recent_saves, 2)

    def test_a_pinned_title_always_wins(self):
        for user in self.users:
            save_anime(user, self.popular)
        self.quiet.is_featured = True
        self.quiet.save(update_fields=["is_featured"])

        picks = selectors.spotlight_animes(limit=2)
        self.assertEqual(picks[0], self.quiet)
        self.assertEqual(picks[1], self.popular)


class SpotlightFallbackTests(TestCase):
    """It must never be empty, on a quiet week or a fresh install."""

    def setUp(self):
        self.user = make_user()
        self.top_viewed = make_anime(name="Most Viewed", view_count=500)
        self.mid = make_anime(name="Middling", view_count=100)
        self.low = make_anime(name="Least Viewed", view_count=1)

    def test_with_no_bookmarks_at_all_it_falls_back_to_views(self):
        picks = selectors.spotlight_animes(limit=2)
        self.assertEqual([anime.name for anime in picks], ["Most Viewed", "Middling"])

    def test_with_only_old_bookmarks_it_falls_back_to_all_time_bookmarks(self):
        save_anime(self.user, self.low, days_ago=90)
        picks = selectors.spotlight_animes(limit=1)
        self.assertEqual(picks[0], self.low)

    def test_it_fills_up_to_the_limit_from_the_next_source(self):
        save_anime(self.user, self.low)
        picks = selectors.spotlight_animes(limit=3)
        self.assertEqual(picks[0], self.low)
        self.assertEqual(len(picks), 3)

    def test_no_title_appears_twice(self):
        save_anime(self.user, self.top_viewed)
        picks = selectors.spotlight_animes(limit=3)
        self.assertEqual(len({anime.pk for anime in picks}), len(picks))

    def test_it_asks_for_no_more_than_the_limit(self):
        self.assertEqual(len(selectors.spotlight_animes(limit=2)), 2)

    def test_an_empty_catalogue_returns_an_empty_spotlight(self):
        from apps.streaming.models import Anime

        Anime.objects.all().delete()
        self.assertEqual(selectors.spotlight_animes(limit=3), [])


class SpotlightViewTests(TestCase):
    def test_the_home_page_says_why_a_title_is_in_the_spotlight(self):
        user = make_user()
        anime = make_anime(name="Trending Pick")
        save_anime(user, anime)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 saved this week")

    def test_a_title_with_no_recent_saves_shows_its_status_instead(self):
        make_anime(name="Quiet Title")
        response = self.client.get("/")
        self.assertNotContains(response, "saved this week")
