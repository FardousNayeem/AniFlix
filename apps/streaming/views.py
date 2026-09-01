"""Catalogue views.

Rules:
- reads go through ``selectors``, writes go through ``services``;
- anything that changes state is POST-only and login-gated;
- missing records return 404, never a 500.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, View

from apps.core.pagination import paginate, querystring_without_page

from . import selectors, services
from .forms import CommentForm, RatingForm
from .models import Anime, Comment, Genre


class CatalogListMixin:
    """Shared search/sort/pagination behaviour for the browse-style pages."""

    genre: Genre | None = None

    def catalog_context(self, request) -> dict:
        search = request.GET.get("q", "").strip()
        sort = request.GET.get("sort", selectors.DEFAULT_SORT)
        if sort not in selectors.SORT_OPTIONS:
            sort = selectors.DEFAULT_SORT

        queryset = selectors.anime_catalog(
            user=request.user, search=search, genre=self.genre, sort=sort
        )
        page = paginate(request, queryset, settings.CATALOG_PAGE_SIZE)
        return {
            "page_obj": page,
            "animes": page.object_list,
            "search": search,
            "sort": sort,
            "sort_options": selectors.SORT_OPTIONS,
            "extra_query": querystring_without_page(request),
            "result_count": page.paginator.count,
            "suggest_url": reverse("streaming:search-suggest"),
        }


class HomeView(CatalogListMixin, TemplateView):
    """Landing page: spotlight, trending rail, then the paginated catalogue."""

    template_name = "streaming/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.catalog_context(self.request))
        is_first_page = context["page_obj"].number == 1 and not context["search"]
        context["show_hero"] = is_first_page
        if is_first_page:
            spotlight = selectors.spotlight_animes()
            context["featured"] = spotlight
            context["spotlight_window_days"] = settings.SPOTLIGHT_WINDOW_DAYS
            context["trending"] = selectors.trending_animes(
                exclude_ids=[item.id for item in spotlight], limit=10
            )
        context["genres"] = selectors.genres_with_counts()[:10]
        return context


class BrowseView(CatalogListMixin, TemplateView):
    """The full catalogue with filters, no hero."""

    template_name = "streaming/browse.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.catalog_context(self.request))
        context["genres"] = selectors.genres_with_counts()
        return context


class GenreListView(TemplateView):
    template_name = "streaming/genre_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["genres"] = selectors.genres_with_counts()
        return context


class GenreDetailView(CatalogListMixin, TemplateView):
    """A real page per genre.

    The old build rendered every genre's whole catalogue into hidden modals on
    one page, so opening /genre downloaded the entire library.
    """

    template_name = "streaming/genre_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        self.genre = get_object_or_404(Genre, slug=kwargs["slug"])
        context.update(self.catalog_context(self.request))
        context["genre"] = self.genre
        context["genres"] = selectors.genres_with_counts()
        return context


class AnimeDetailView(View):
    def get(self, request, slug: str):
        anime = selectors.anime_detail(slug=slug)
        services.register_view(anime)

        rating = selectors.user_rating(request.user, anime)
        context = {
            "anime": anime,
            "episodes": selectors.episodes_for(anime),
            "related": selectors.related_animes(anime),
            "in_watchlist": selectors.is_in_watchlist(request.user, anime),
            "user_score": rating.score if rating else None,
            "rating_range": range(1, 6),
        }
        return render(request, "streaming/anime_detail.html", context)


@login_required
@require_POST
def rate_anime_view(request, slug: str):
    """Answers JSON to the in-page star control, redirects for no-JS clients."""
    anime = get_object_or_404(Anime, slug=slug)
    form = RatingForm(request.POST)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not form.is_valid():
        message = "Pick a score between 1 and 5."
        if is_ajax:
            return JsonResponse({"ok": False, "message": message}, status=400)
        messages.error(request, message)
        return redirect(anime)

    score = form.cleaned_data["score"]
    try:
        services.rate_anime(user=request.user, anime=anime, score=score)
    except ValidationError as exc:
        if is_ajax:
            return JsonResponse({"ok": False, "message": exc.messages[0]}, status=400)
        messages.error(request, exc.messages[0])
        return redirect(anime)

    message = f"Rated {anime.name} {score}/5."
    if is_ajax:
        summary = selectors.rating_summary(anime)
        return JsonResponse(
            {
                "ok": True,
                "message": message,
                "average": summary["average_display"],
                "average_value": summary["average"] or 0,
                "count_label": summary["count_label"],
            }
        )

    messages.success(request, message)
    return redirect(anime)


@login_required
@require_POST
def toggle_watchlist_view(request, slug: str):
    """Answers JSON for the in-page button, redirects for no-JS clients."""
    anime = get_object_or_404(Anime, slug=slug)
    in_watchlist = services.toggle_watchlist(user=request.user, anime=anime)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse(
            {
                "in_watchlist": in_watchlist,
                "label": "In My List" if in_watchlist else "Add to My List",
            }
        )

    messages.success(
        request,
        f"{anime.name} added to My List." if in_watchlist else f"{anime.name} removed from My List.",
    )
    return redirect(request.META.get("HTTP_REFERER") or anime.get_absolute_url())


class EpisodeDetailView(View):
    """Player page. Episodes are addressed by anime slug + number, not a bare id."""

    def get(self, request, slug: str, number: int):
        episode = selectors.episode_for(anime_slug=slug, number=number)
        previous_episode, next_episode = selectors.adjacent_episodes(episode)
        context = {
            "episode": episode,
            "anime": episode.anime,
            "comments": selectors.comments_for(episode),
            "comment_form": CommentForm(),
            "previous_episode": previous_episode,
            "next_episode": next_episode,
            "episodes": selectors.episodes_for(episode.anime),
        }
        return render(request, "streaming/episode_detail.html", context)

    def post(self, request, slug: str, number: int):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        episode = selectors.episode_for(anime_slug=slug, number=number)
        form = CommentForm(request.POST)
        if form.is_valid():
            services.post_comment(user=request.user, episode=episode, body=form.cleaned_data["body"])
            messages.success(request, "Comment posted.")
            return redirect(episode.get_absolute_url() + "#comments")

        context = {
            "episode": episode,
            "anime": episode.anime,
            "comments": selectors.comments_for(episode),
            "comment_form": form,
            "episodes": selectors.episodes_for(episode.anime),
        }
        previous_episode, next_episode = selectors.adjacent_episodes(episode)
        context["previous_episode"] = previous_episode
        context["next_episode"] = next_episode
        return render(request, "streaming/episode_detail.html", context, status=400)


@login_required
@require_POST
def delete_comment_view(request, pk: int):
    comment = get_object_or_404(Comment.objects.select_related("episode__anime"), pk=pk)
    target = comment.episode.get_absolute_url()
    try:
        services.delete_comment(user=request.user, comment=comment)
    except PermissionError:
        messages.error(request, "You can only delete your own comments.")
    else:
        messages.success(request, "Comment deleted.")
    return redirect(target + "#comments")


def search_suggest_view(request):
    """JSON for the instant-search dropdown. Read-only and public."""
    results = selectors.search_suggestions(term=request.GET.get("q", ""))
    return JsonResponse({"results": results})
