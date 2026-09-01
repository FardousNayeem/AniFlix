from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from .models import Anime, Comment, Episode, Genre, Rating, WatchlistEntry


class EpisodeInline(admin.TabularInline):
    model = Episode
    extra = 1
    fields = ["number", "title", "air_date", "duration_minutes", "video_url"]
    ordering = ["number"]


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ["name", "anime_count"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_anime_count=Count("animes"))

    @admin.display(description="Titles", ordering="_anime_count")
    def anime_count(self, obj) -> int:
        return obj._anime_count


@admin.register(Anime)
class AnimeAdmin(admin.ModelAdmin):
    list_display = ["name", "studio", "release_year", "status", "is_featured", "view_count", "poster_preview"]
    list_filter = ["status", "is_featured", "release_year", "genres"]
    list_editable = ["is_featured"]
    search_fields = ["name", "studio", "synopsis"]
    filter_horizontal = ["genres"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["view_count", "created_at", "updated_at", "poster_preview"]
    inlines = [EpisodeInline]
    save_on_top = True
    fieldsets = (
        (None, {"fields": ("name", "slug", "studio", "release_year", "status", "genres")}),
        ("Story", {"fields": ("synopsis",)}),
        ("Artwork", {"fields": ("poster", "poster_preview", "backdrop", "is_featured")}),
        ("Stats", {"fields": ("view_count", "created_at", "updated_at")}),
    )

    @admin.display(description="Poster")
    def poster_preview(self, obj):
        if obj.poster_url:
            return format_html('<img src="{}" style="height:90px;border-radius:6px" />', obj.poster_url)
        return "No poster"


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    list_display = ["anime", "number", "title", "air_date", "is_playable"]
    list_filter = ["anime", "air_date"]
    search_fields = ["title", "anime__name"]
    list_select_related = ["anime"]

    @admin.display(boolean=True, description="Playable")
    def is_playable(self, obj) -> bool:
        return obj.is_playable


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    list_display = ["user", "anime", "score", "created_at"]
    list_filter = ["score"]
    search_fields = ["user__email", "anime__name"]
    list_select_related = ["user", "anime"]


@admin.register(WatchlistEntry)
class WatchlistEntryAdmin(admin.ModelAdmin):
    list_display = ["user", "anime", "created_at"]
    search_fields = ["user__email", "anime__name"]
    list_select_related = ["user", "anime"]


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ["user", "episode", "short_body", "created_at"]
    search_fields = ["user__email", "body", "episode__title"]
    list_select_related = ["user", "episode__anime"]

    @admin.display(description="Comment")
    def short_body(self, obj) -> str:
        return obj.body[:60]
