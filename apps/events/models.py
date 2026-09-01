"""Anime events and the registrations against them."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import TimeStampedModel


def event_cover_upload_to(instance: "Event", filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"events/{uuid.uuid4().hex}.{suffix}"


class Venue(models.Model):
    """Renamed from ``Location``: a venue is a place an event happens at."""

    name = models.CharField(max_length=200)
    address = models.CharField(max_length=350)
    city = models.CharField(max_length=120, blank=True)
    map_url = models.URLField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name}, {self.city}" if self.city else self.name


class EventQuerySet(models.QuerySet):
    def published(self) -> "EventQuerySet":
        return self.filter(is_published=True)

    def upcoming(self) -> "EventQuerySet":
        return self.filter(starts_at__gte=timezone.now()).order_by("starts_at")

    def past(self) -> "EventQuerySet":
        return self.filter(starts_at__lt=timezone.now()).order_by("-starts_at")


class Event(TimeStampedModel):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    summary = models.CharField(max_length=280, blank=True, help_text="One-line teaser used on cards.")
    description = models.TextField()
    cover = models.ImageField(upload_to=event_cover_upload_to, blank=True, null=True)
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="events")
    host_name = models.CharField(max_length=200, blank=True)
    organiser_email = models.EmailField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    capacity = models.PositiveIntegerField(
        default=0, help_text="0 means unlimited seats."
    )
    is_published = models.BooleanField(default=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ["starts_at"]
        indexes = [models.Index(fields=["starts_at"])]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:200] or "event"
            candidate = base
            suffix = 2
            while Event.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("events:detail", kwargs={"slug": self.slug})

    @property
    def cover_url(self) -> str | None:
        if self.cover and hasattr(self.cover, "url"):
            try:
                return self.cover.url
            except ValueError:  # pragma: no cover
                return None
        return None

    @property
    def has_started(self) -> bool:
        return self.starts_at < timezone.now()

    @property
    def seats_taken(self) -> int:
        return self.registrations.count()

    @property
    def seats_left(self) -> int | None:
        """``None`` means unlimited."""
        if self.capacity == 0:
            return None
        return max(0, self.capacity - self.seats_taken)

    @property
    def is_full(self) -> bool:
        left = self.seats_left
        return left is not None and left == 0

    @property
    def is_open(self) -> bool:
        return self.is_published and not self.has_started and not self.is_full


class Registration(TimeStampedModel):
    """One seat. Replaces ``Contributor``, which allowed unlimited duplicates."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_registrations"
    )
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="registrations")
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=32, blank=True)
    reference = models.CharField(max_length=12, unique=True, editable=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "event"], name="unique_registration_per_user_per_event")
        ]

    def __str__(self) -> str:
        return f"{self.user} -> {self.event}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = uuid.uuid4().hex[:10].upper()
        super().save(*args, **kwargs)
