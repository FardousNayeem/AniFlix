"""Read-side queries for events."""

from __future__ import annotations

from django.db.models import Count, Exists, OuterRef, QuerySet
from django.shortcuts import get_object_or_404

from .models import Event, Registration


def _with_counts(queryset: QuerySet[Event], user=None) -> QuerySet[Event]:
    queryset = queryset.select_related("venue").annotate(registration_count=Count("registrations"))
    if user is not None and user.is_authenticated:
        queryset = queryset.annotate(
            is_registered=Exists(Registration.objects.filter(user=user, event=OuterRef("pk")))
        )
    return queryset


def upcoming_events(*, user=None) -> QuerySet[Event]:
    return _with_counts(Event.objects.published().upcoming(), user)


def past_events(*, user=None, limit: int = 6) -> QuerySet[Event]:
    return _with_counts(Event.objects.published().past(), user)[:limit]


def event_detail(*, slug: str) -> Event:
    return get_object_or_404(Event.objects.select_related("venue").published(), slug=slug)


def registration_for(user, event: Event) -> Registration | None:
    if not user.is_authenticated:
        return None
    return Registration.objects.filter(user=user, event=event).first()


def registrations_for_user(user) -> QuerySet[Registration]:
    if not user.is_authenticated:
        return Registration.objects.none()
    return (
        Registration.objects.filter(user=user)
        .select_related("event", "event__venue")
        .order_by("event__starts_at")
    )
