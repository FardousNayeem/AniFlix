"""Write-side operations for the catalogue.

Every state change lives here. Views validate input and call one function, so
the same rule cannot be enforced two different ways in two different places.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from .models import RATING_MAX, RATING_MIN, Anime, Comment, Episode, Rating, WatchlistEntry

logger = logging.getLogger(__name__)


def register_view(anime: Anime) -> None:
    """Increment the view counter atomically.

    ``F()`` avoids the read-modify-write race the previous code had, and the
    in-memory instance is refreshed so the template shows the new number.
    """
    Anime.objects.filter(pk=anime.pk).update(view_count=F("view_count") + 1)
    anime.view_count = Anime.objects.values_list("view_count", flat=True).get(pk=anime.pk)


def rate_anime(*, user, anime: Anime, score: int) -> Rating:
    """Create or replace this user's score. Idempotent by design."""
    if not RATING_MIN <= score <= RATING_MAX:
        raise ValidationError(f"Rating must be between {RATING_MIN} and {RATING_MAX}.")
    rating, _created = Rating.objects.update_or_create(
        user=user, anime=anime, defaults={"score": score}
    )
    return rating


def toggle_watchlist(*, user, anime: Anime) -> bool:
    """Add or remove a bookmark. Returns the resulting state."""
    with transaction.atomic():
        entry = WatchlistEntry.objects.filter(user=user, anime=anime).first()
        if entry is not None:
            entry.delete()
            return False
        WatchlistEntry.objects.create(user=user, anime=anime)
        return True


def remove_from_watchlist(*, user, anime: Anime) -> None:
    WatchlistEntry.objects.filter(user=user, anime=anime).delete()


def post_comment(*, user, episode: Episode, body: str) -> Comment:
    body = (body or "").strip()
    if not body:
        raise ValidationError("Write something before posting.")
    if len(body) > 2000:
        raise ValidationError("Comments are limited to 2000 characters.")
    return Comment.objects.create(user=user, episode=episode, body=body)


def delete_comment(*, user, comment: Comment) -> None:
    """Only the author or a staff member may delete."""
    if comment.user_id != user.id and not user.is_staff:
        raise PermissionError("You can only delete your own comments.")
    comment.delete()
