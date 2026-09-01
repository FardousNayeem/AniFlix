from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.streaming import services
from apps.streaming.models import Anime, Comment, Rating, WatchlistEntry

from .factories import make_anime, make_episode, make_user


class RatingServiceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.anime = make_anime()

    def test_rating_twice_updates_instead_of_duplicating(self):
        """The old view inserted a new row per submission, so averages drifted."""
        services.rate_anime(user=self.user, anime=self.anime, score=3)
        services.rate_anime(user=self.user, anime=self.anime, score=5)

        self.assertEqual(Rating.objects.filter(user=self.user, anime=self.anime).count(), 1)
        self.assertEqual(Rating.objects.get(user=self.user, anime=self.anime).score, 5)

    def test_out_of_range_scores_are_rejected(self):
        for score in (0, 6, -1, 99):
            with self.subTest(score=score), self.assertRaises(ValidationError):
                services.rate_anime(user=self.user, anime=self.anime, score=score)

    def test_two_users_can_rate_the_same_title(self):
        other = make_user("other@example.com")
        services.rate_anime(user=self.user, anime=self.anime, score=4)
        services.rate_anime(user=other, anime=self.anime, score=2)
        self.assertEqual(Rating.objects.filter(anime=self.anime).count(), 2)


class WatchlistServiceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.anime = make_anime()

    def test_toggle_adds_then_removes(self):
        self.assertTrue(services.toggle_watchlist(user=self.user, anime=self.anime))
        self.assertEqual(WatchlistEntry.objects.count(), 1)

        self.assertFalse(services.toggle_watchlist(user=self.user, anime=self.anime))
        self.assertEqual(WatchlistEntry.objects.count(), 0)

    def test_removing_something_absent_is_not_an_error(self):
        services.remove_from_watchlist(user=self.user, anime=self.anime)
        self.assertEqual(WatchlistEntry.objects.count(), 0)


class ViewCounterTests(TestCase):
    def test_view_count_increments_and_refreshes_the_instance(self):
        anime = make_anime()
        services.register_view(anime)
        services.register_view(anime)

        self.assertEqual(anime.view_count, 2)
        self.assertEqual(Anime.objects.get(pk=anime.pk).view_count, 2)


class CommentServiceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.anime = make_anime()
        self.episode = make_episode(self.anime)

    def test_blank_comments_are_rejected(self):
        for body in ("", "   ", "\n\t "):
            with self.subTest(body=repr(body)), self.assertRaises(ValidationError):
                services.post_comment(user=self.user, episode=self.episode, body=body)

    def test_overlong_comments_are_rejected(self):
        with self.assertRaises(ValidationError):
            services.post_comment(user=self.user, episode=self.episode, body="x" * 2001)

    def test_only_the_author_or_staff_may_delete(self):
        comment = services.post_comment(user=self.user, episode=self.episode, body="Great arc.")
        stranger = make_user("stranger@example.com")

        with self.assertRaises(PermissionError):
            services.delete_comment(user=stranger, comment=comment)
        self.assertEqual(Comment.objects.count(), 1)

        staff = make_user("staff@example.com", is_staff=True)
        services.delete_comment(user=staff, comment=comment)
        self.assertEqual(Comment.objects.count(), 0)

