"""Video resolution.

Covers every URL shape present in the imported catalogue plus the forms a
person is likely to paste into the admin.
"""

from django.test import TestCase

from apps.streaming.video import resolve

from .factories import make_anime, make_episode


class SchemeTests(TestCase):
    def test_http_is_upgraded_to_https(self):
        """Mixed content is blocked with no visible error on an HTTPS page."""
        source = resolve("http://www.animegg.org/embed/12800")
        self.assertEqual(source.url, "https://www.animegg.org/embed/12800")

    def test_protocol_relative_urls_get_a_scheme(self):
        source = resolve("//www.animegg.org/embed/79303")
        self.assertEqual(source.url, "https://www.animegg.org/embed/79303")

    def test_a_bare_host_is_assumed_to_be_https(self):
        self.assertEqual(resolve("example.com/embed/9").url, "https://example.com/embed/9")

    def test_blank_and_junk_resolve_to_nothing_playable(self):
        for raw in ["", "   ", None, "not a url"]:
            with self.subTest(raw=raw):
                self.assertFalse(resolve(raw).is_playable)


class YouTubeTests(TestCase):
    EXPECTED = "https://www.youtube-nocookie.com/embed/tUor3kmmQ8A?rel=0&modestbranding=1&playsinline=1"

    def test_a_watch_url_becomes_an_embed_url(self):
        """A watch URL cannot be framed at all; this was the original failure."""
        source = resolve("https://www.youtube.com/watch?v=tUor3kmmQ8A")
        self.assertEqual(source.url, self.EXPECTED)
        self.assertTrue(source.is_embed)

    def test_a_short_youtu_be_link_becomes_an_embed_url(self):
        self.assertEqual(resolve("https://youtu.be/tUor3kmmQ8A").url, self.EXPECTED)

    def test_a_shorts_link_becomes_an_embed_url(self):
        self.assertEqual(resolve("https://www.youtube.com/shorts/tUor3kmmQ8A").url, self.EXPECTED)

    def test_an_existing_embed_link_is_cleaned_of_share_tracking(self):
        source = resolve("https://www.youtube.com/embed/tUor3kmmQ8A?si=5S7nAiKe3kN1nLhe")
        self.assertEqual(source.url, self.EXPECTED)
        self.assertNotIn("si=", source.url)

    def test_a_start_offset_in_seconds_is_carried_over(self):
        self.assertIn("start=90", resolve("https://youtu.be/tUor3kmmQ8A?t=90").url)

    def test_a_start_offset_in_minutes_and_seconds_is_converted(self):
        self.assertIn("start=90", resolve("https://www.youtube.com/watch?v=tUor3kmmQ8A&t=1m30s").url)

    def test_the_open_in_a_tab_link_points_at_the_normal_watch_page(self):
        source = resolve("https://youtu.be/tUor3kmmQ8A")
        self.assertEqual(source.link_url, "https://www.youtube.com/watch?v=tUor3kmmQ8A")
        self.assertEqual(source.provider, "YouTube")

    def test_a_playlist_url_is_not_guessed_at(self):
        url = "https://www.youtube.com/playlist?list=PLabc"
        source = resolve(url)
        self.assertEqual(source.url, url)


class VimeoAndDailymotionTests(TestCase):
    def test_a_vimeo_page_url_becomes_a_player_url(self):
        source = resolve("https://vimeo.com/76979871")
        self.assertEqual(source.url, "https://player.vimeo.com/video/76979871")
        self.assertEqual(source.provider, "Vimeo")

    def test_an_existing_vimeo_player_url_is_kept(self):
        source = resolve("https://player.vimeo.com/video/76979871")
        self.assertEqual(source.url, "https://player.vimeo.com/video/76979871")

    def test_a_dailymotion_page_url_becomes_an_embed_url(self):
        source = resolve("https://www.dailymotion.com/video/x8abcde")
        self.assertEqual(source.url, "https://www.dailymotion.com/embed/video/x8abcde")

    def test_a_dai_ly_short_link_becomes_an_embed_url(self):
        self.assertEqual(
            resolve("https://dai.ly/x8abcde").url,
            "https://www.dailymotion.com/embed/video/x8abcde",
        )


class DirectFileTests(TestCase):
    def test_an_mp4_plays_natively_rather_than_in_a_frame(self):
        source = resolve("https://cdn.example.com/ep1.mp4")
        self.assertTrue(source.is_file)
        self.assertFalse(source.is_embed)
        self.assertEqual(source.mime, "video/mp4")

    def test_webm_and_ogv_are_recognised(self):
        self.assertEqual(resolve("https://cdn.example.com/a.webm").mime, "video/webm")
        self.assertEqual(resolve("https://cdn.example.com/a.ogv").mime, "video/ogg")

    def test_an_hls_playlist_is_treated_as_a_file(self):
        self.assertTrue(resolve("https://cdn.example.com/master.m3u8").is_file)


class UnknownHostTests(TestCase):
    def test_a_third_party_embed_page_is_framed_as_is(self):
        """Most anime mirrors publish a purpose-built /embed/ page."""
        source = resolve("https://www.animegg.org/embed/12800")
        self.assertTrue(source.is_embed)
        self.assertEqual(source.url, "https://www.animegg.org/embed/12800")
        self.assertEqual(source.provider, "animegg.org")


class EpisodeIntegrationTests(TestCase):
    def setUp(self):
        self.anime = make_anime()

    def test_an_episode_exposes_its_resolved_source(self):
        episode = make_episode(self.anime, number=1, video_url="http://www.animegg.org/embed/12800")
        self.assertTrue(episode.is_playable)
        self.assertEqual(episode.source.url, "https://www.animegg.org/embed/12800")

    def test_an_episode_without_a_link_is_not_playable(self):
        episode = make_episode(self.anime, number=2, video_url="")
        self.assertFalse(episode.is_playable)
        self.assertEqual(episode.source.kind, "none")

    def test_the_player_renders_an_iframe_for_an_embed(self):
        episode = make_episode(self.anime, number=3, video_url="https://youtu.be/tUor3kmmQ8A")
        response = self.client.get(episode.get_absolute_url())
        self.assertContains(response, "data-player-frame")
        self.assertContains(response, "youtube-nocookie.com/embed/tUor3kmmQ8A")

    def test_the_player_renders_a_video_element_for_a_direct_file(self):
        episode = make_episode(self.anime, number=4, video_url="https://cdn.example.com/ep.mp4")
        response = self.client.get(episode.get_absolute_url())
        self.assertContains(response, "<video controls")
        self.assertNotContains(response, "data-player-frame")

    def test_an_unavailable_episode_says_so_instead_of_showing_a_dead_frame(self):
        episode = make_episode(self.anime, number=5, video_url="")
        response = self.client.get(episode.get_absolute_url())
        self.assertContains(response, "not available yet")
        self.assertNotContains(response, "data-player-frame")
