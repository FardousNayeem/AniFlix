"""Read-side queries for the catalogue.

Views call these instead of building querysets inline, so N+1 fixes happen in
one place and can be tested without a request.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db.models import (
    Avg,
    BooleanField,
    Count,
    Exists,
    F,
    OuterRef,
    Q,
    QuerySet,
    Value,
)
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import Anime, Comment, Episode, Genre, Rating, WatchlistEntry

SORT_OPTIONS = {
    "newest": ("-release_year", "Newest first"),
    "trending": ("-view_count", "Trending"),
    "rating": ("-average_rating", "Top rated"),
    "oldest": ("release_year", "Oldest first"),
    "az": ("name", "A to Z"),
}
DEFAULT_SORT = "newest"


def anime_catalog(
    *,
    user=None,
    search: str = "",
    genre: Genre | None = None,
    sort: str = DEFAULT_SORT,
) -> QuerySet[Anime]:
    """The one queryset behind the homepage, genre pages and search."""
    queryset = Anime.objects.with_card_data()

    if genre is not None:
        queryset = queryset.filter(genres=genre)
    queryset = queryset.search(search)
    queryset = annotate_watchlist_state(queryset, user)

    order_field = SORT_OPTIONS.get(sort, SORT_OPTIONS[DEFAULT_SORT])[0]
    if order_field == "-average_rating":
        # NULLs last: unrated titles must not outrank rated ones.
        return queryset.order_by(F("average_rating").desc(nulls_last=True), "name")
    return queryset.order_by(order_field, "name")


def annotate_watchlist_state(queryset: QuerySet[Anime], user) -> QuerySet[Anime]:
    """Add ``in_watchlist`` without a query per card."""
    if user is None or not user.is_authenticated:
        return queryset.annotate(in_watchlist=Value(False, output_field=BooleanField()))
    return queryset.annotate(
        in_watchlist=Exists(WatchlistEntry.objects.filter(user=user, anime=OuterRef("pk")))
    )


def spotlight_animes(*, limit: int | None = None, window_days: int | None = None) -> list[Anime]:
    """Titles for the homepage spotlight, ranked by how many people saved them.

    The spotlight answers "what is the fandom picking up right now", so the
    ranking is bookmarks added inside a rolling window (seven days by default),
    not lifetime view count. A view is passive and never decays; a bookmark is
    a deliberate act, and counting only recent ones lets a new title overtake
    an old one instead of the same few titles sitting there forever.

    Sources are tried in order until the spotlight is full, so it is never
    empty on a quiet week or a fresh install:

    1. Titles pinned by staff (``is_featured``), which always win.
    2. Most bookmarked inside the window.
    3. Most bookmarked all time.
    4. Most viewed.

    Every result carries ``recent_saves``, so the template can say why a title
    is there.
    """
    limit = limit or settings.SPOTLIGHT_SIZE
    window_days = window_days if window_days is not None else settings.SPOTLIGHT_WINDOW_DAYS
    since = timezone.now() - timedelta(days=window_days)

    base = Anime.objects.prefetch_related("genres").annotate(
        recent_saves=Count(
            "watchlist_entries",
            filter=Q(watchlist_entries__created_at__gte=since),
            distinct=True,
        ),
        total_saves=Count("watchlist_entries", distinct=True),
    )

    picks: list[Anime] = []
    seen: set[int] = set()

    def fill(queryset) -> bool:
        """Add titles until the spotlight is full. Returns True when it is."""
        for anime in queryset[: limit - len(picks)]:
            if anime.pk in seen:
                continue
            seen.add(anime.pk)
            picks.append(anime)
        return len(picks) >= limit

    sources = (
        base.filter(is_featured=True).order_by("-recent_saves", "-view_count"),
        base.filter(recent_saves__gt=0).order_by("-recent_saves", "-view_count", "name"),
        base.filter(total_saves__gt=0).order_by("-total_saves", "-view_count", "name"),
        base.order_by("-view_count", "name"),
    )
    for source in sources:
        if fill(source.exclude(pk__in=seen) if seen else source):
            break

    return picks


def trending_animes(*, exclude_ids: list[int] | None = None, limit: int = 8) -> QuerySet[Anime]:
    queryset = Anime.objects.with_card_data().order_by("-view_count")
    if exclude_ids:
        queryset = queryset.exclude(id__in=exclude_ids)
    return queryset[:limit]


def genres_with_counts() -> QuerySet[Genre]:
    return Genre.objects.annotate(anime_count=Count("animes")).order_by("-anime_count", "name")


def anime_detail(*, slug: str) -> Anime:
    return get_object_or_404(
        Anime.objects.prefetch_related("genres").annotate(
            average_rating=Avg("ratings__score"),
            rating_count=Count("ratings", distinct=True),
        ),
        slug=slug,
    )


def episodes_for(anime: Anime) -> QuerySet[Episode]:
    return anime.episodes.all().order_by("number")


def related_animes(anime: Anime, *, limit: int = 6) -> QuerySet[Anime]:
    """Same-genre suggestions, most viewed first."""
    genre_ids = list(anime.genres.values_list("id", flat=True))
    if not genre_ids:
        return Anime.objects.none()
    return (
        Anime.objects.with_card_data()
        .filter(genres__id__in=genre_ids)
        .exclude(pk=anime.pk)
        .distinct()
        .order_by("-view_count")[:limit]
    )


def rating_summary(anime: Anime) -> dict:
    """Current average and vote count, recomputed after a rating is saved."""
    aggregate = Rating.objects.filter(anime=anime).aggregate(
        average=Avg("score"), count=Count("id")
    )
    average = aggregate["average"]
    count = aggregate["count"] or 0
    return {
        "average": average,
        "average_display": f"{average:.1f}" if average else "--",
        "count": count,
        "count_label": (
            f"from {count} rating{'' if count == 1 else 's'}" if count else "not rated yet"
        ),
    }


def user_rating(user, anime: Anime) -> Rating | None:
    if not user.is_authenticated:
        return None
    return Rating.objects.filter(user=user, anime=anime).first()


def is_in_watchlist(user, anime: Anime) -> bool:
    if not user.is_authenticated:
        return False
    return WatchlistEntry.objects.filter(user=user, anime=anime).exists()


def watchlist_for_user(user) -> QuerySet[Anime]:
    if not user.is_authenticated:
        return Anime.objects.none()
    return (
        Anime.objects.with_card_data()
        .filter(watchlist_entries__user=user)
        .order_by("-watchlist_entries__created_at")
    )


def comments_for(episode: Episode) -> QuerySet[Comment]:
    return episode.comments.select_related("user").order_by("-created_at")


def episode_for(*, anime_slug: str, number: int) -> Episode:
    return get_object_or_404(
        Episode.objects.select_related("anime"),
        anime__slug=anime_slug,
        number=number,
    )


def adjacent_episodes(episode: Episode) -> tuple[Episode | None, Episode | None]:
    """Previous/next for the player's navigation."""
    siblings = episode.anime.episodes.order_by("number")
    previous = siblings.filter(number__lt=episode.number).last()
    following = siblings.filter(number__gt=episode.number).first()
    return previous, following


def search_suggestions(*, term: str, limit: int = 6) -> list[dict]:
    """Payload for the instant-search dropdown.

    Deliberately small: id, name, year and poster only. Capped so a one-letter
    query cannot ask the database for the whole catalogue.
    """
    term = (term or "").strip()
    if len(term) < 2:
        return []

    matches = (
        Anime.objects.filter(
            Q(name__icontains=term) | Q(studio__icontains=term) | Q(genres__name__icontains=term)
        )
        .distinct()
        .order_by("-view_count", "name")[:limit]
    )
    return [
        {
            "name": anime.name,
            "year": anime.release_year,
            "studio": anime.studio,
            "url": anime.get_absolute_url(),
            "poster": anime.poster_url or "",
        }
        for anime in matches
    ]
