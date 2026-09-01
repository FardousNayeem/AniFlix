"""Small builders so tests read as scenarios, not as ORM setup."""

from apps.accounts.models import User
from apps.streaming.models import Anime, Episode, Genre


def make_user(email="fan@example.com", **kwargs) -> User:
    return User.objects.create_user(email=email, password="test-pass-123", **kwargs)


def make_anime(name="Terror in Resonance", **kwargs) -> Anime:
    defaults = {
        "studio": "MAPPA",
        "release_year": 2014,
        "synopsis": "Two teenagers set off a bomb and dare Tokyo to work out why.",
    }
    defaults.update(kwargs)
    return Anime.objects.create(name=name, **defaults)


def make_genre(name="Thriller") -> Genre:
    return Genre.objects.create(name=name)


def make_episode(anime, number=1, **kwargs) -> Episode:
    defaults = {"title": f"Episode {number}", "video_url": "https://example.test/watch"}
    defaults.update(kwargs)
    return Episode.objects.create(anime=anime, number=number, **defaults)
