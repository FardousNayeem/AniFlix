"""Write-side operations for events."""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import Event, Registration

logger = logging.getLogger(__name__)


def register_for_event(*, user, event: Event, contact_email: str, contact_phone: str = "") -> Registration:
    """Claim a seat.

    Guards the three cases the old GET-based handler ignored: the event has
    already happened, the event is full, and the user is already registered.
    """
    if not event.is_published:
        raise ValidationError("This event is not open for registration.")
    if event.has_started:
        raise ValidationError("This event has already started.")

    with transaction.atomic():
        # Lock the row so two concurrent requests cannot oversell the last seat.
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        if locked_event.is_full:
            raise ValidationError("This event is fully booked.")
        try:
            return Registration.objects.create(
                user=user,
                event=locked_event,
                contact_email=contact_email,
                contact_phone=contact_phone,
            )
        except IntegrityError as exc:
            logger.info("Duplicate registration attempt user=%s event=%s", user.pk, event.pk)
            raise ValidationError("You are already registered for this event.") from exc


def cancel_registration(*, user, event: Event) -> bool:
    """Give the seat back. Returns whether anything was cancelled."""
    if event.has_started:
        raise ValidationError("This event has already started, so it can no longer be cancelled.")
    deleted, _ = Registration.objects.filter(user=user, event=event).delete()
    return bool(deleted)
