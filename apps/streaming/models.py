"""Catalogue models: genres, anime, episodes, and what users do with them."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.text import slugify

from apps.core.models import TimeStampedModel

from .video import VideoSource
from .video import resolve as resolve_video

RATING_MIN = 1
RATING_MAX = 5


def anime_poster_upload_to(instance: "Anime", filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"anime/posters/{uuid.uuid4().hex}.{suffix}"


def anime_backdrop_upload_to(instance: "Anime", filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"anime/backdrops/{uuid.uuid4().hex}.{suffix}"


class SlugModel(models.Model):
    """Slug generation shared by ``Genre`` and ``Anime``."""

    slug = models.SlugField(max_length=220, unique=True, blank=True)

    class Meta:
        abstract = True

    slug_source_field = "name"

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(getattr(self, self.slug_source_field) or "")[:200] or "item"
            candidate = base
            suffix = 2
            model = type(self)
            while model.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)


class Genre(SlugModel):
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=280, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("streaming:genre-detail", kwargs={"slug": self.slug})


class AnimeQuerySet(models.QuerySet):
    def with_card_data(self) -> "AnimeQuerySet":
        """Everything a poster card renders, in one query per relation."""
        return self.prefetch_related("genres").annotate(
            average_rating=models.Avg("ratings__score"),
            rating_count=models.Count("ratings", distinct=True),
            episode_count=models.Count("episodes", distinct=True),
        )

    def search(self, term: str) -> "AnimeQuerySet":
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(
            models.Q(name__icontains=term)
            | models.Q(studio__icontains=term)
            | models.Q(synopsis__icontains=term)
            | models.Q(genres__name__icontains=term)
        ).distinct()


class Anime(SlugModel, TimeStampedModel):
    class Status(models.TextChoices):
        ONGOING = "ongoing", "Ongoing"
        COMPLETED = "completed", "Completed"
        UPCOMING = "upcoming", "Upcoming"

    name = models.CharField(max_length=200)
    studio = models.CharField(max_length=200, blank=True)
    release_year = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1900), MaxValueValidator(2100)]
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    synopsis = models.TextField()
    poster = models.ImageField(upload_to=anime_poster_upload_to, blank=True, null=True)
    backdrop = models.ImageField(
        upload_to=anime_backdrop_upload_to,
        blank=True,
        null=True,
        help_text="Wide 16:9 art used by the homepage spotlight.",
    )
    is_featured = models.BooleanField(
        default=False,
        help_text=(
            "Pin this title to the homepage spotlight. Leave off and the "
            "spotlight ranks titles by how many people bookmarked them recently."
        ),
    )
    view_count = models.PositiveIntegerField(default=0, editable=False)
    genres = models.ManyToManyField(Genre, related_name="animes", blank=True)
    anilist_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text=(
            "The AniList entry this title is. Set by the catalogue seeder and "
            "by `train_recommender`, and used to tell a recommendation that we "
            "carry from one we do not."
        ),
    )

    objects = AnimeQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "name"]
        indexes = [
            models.Index(fields=["-view_count"]),
            models.Index(fields=["release_year"]),
        ]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("streaming:anime-detail", kwargs={"slug": self.slug})

    @property
    def poster_url(self) -> str | None:
        if self.poster and hasattr(self.poster, "url"):
            try:
                return self.poster.url
            except ValueError:  # pragma: no cover
                return None
        return None

    @property
    def backdrop_url(self) -> str | None:
        if self.backdrop and hasattr(self.backdrop, "url"):
            try:
                return self.backdrop.url
            except ValueError:  # pragma: no cover
                return None
        return self.poster_url


class Episode(TimeStampedModel):
    anime = models.ForeignKey(Anime, on_delete=models.CASCADE, related_name="episodes")
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    synopsis = models.TextField(blank=True)
    air_date = models.DateField(null=True, blank=True)
    duration_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    video_url = models.URLField(
        blank=True, help_text="Embed or watch URL. Leave blank while an episode is unavailable."
    )

    class Meta:
        ordering = ["anime", "number"]
        constraints = [
            models.UniqueConstraint(fields=["anime", "number"], name="unique_episode_number_per_anime")
        ]

    def __str__(self) -> str:
        return f"{self.anime.name} - Episode {self.number}"

    def get_absolute_url(self) -> str:
        return reverse(
            "streaming:episode-detail",
            kwargs={"slug": self.anime.slug, "number": self.number},
        )

    @cached_property
    def source(self) -> VideoSource:
        """The playable form of ``video_url``. See ``apps.streaming.video``."""
        return resolve_video(self.video_url)

    @property
    def is_playable(self) -> bool:
        return self.source.is_playable


class Rating(TimeStampedModel):
    """One score per user per anime, enforced by the database.

    The previous build inserted a new row on every submission, so the average
    drifted every time somebody re-rated a title.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ratings")
    anime = models.ForeignKey(Anime, on_delete=models.CASCADE, related_name="ratings")
    score = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(RATING_MIN), MaxValueValidator(RATING_MAX)]
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "anime"], name="unique_rating_per_user_per_anime"),
            models.CheckConstraint(
                condition=models.Q(score__gte=RATING_MIN) & models.Q(score__lte=RATING_MAX),
                name="rating_score_within_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} rated {self.anime} {self.score}/5"


class WatchlistEntry(TimeStampedModel):
    """A bookmark. Replaces the ``Anime.favorites`` M2M so it can carry a date."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watchlist_entries"
    )
    anime = models.ForeignKey(Anime, on_delete=models.CASCADE, related_name="watchlist_entries")

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "watchlist entries"
        constraints = [
            models.UniqueConstraint(fields=["user", "anime"], name="unique_watchlist_entry")
        ]

    def __str__(self) -> str:
        return f"{self.user} saved {self.anime}"


class Comment(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comments")
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, related_name="comments")
    body = models.TextField(max_length=2000)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.body[:50]
