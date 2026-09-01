from django.urls import path

from . import views

app_name = "streaming"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("browse/", views.BrowseView.as_view(), name="browse"),
    path("search/suggest/", views.search_suggest_view, name="search-suggest"),
    path("genres/", views.GenreListView.as_view(), name="genre-list"),
    path("genres/<slug:slug>/", views.GenreDetailView.as_view(), name="genre-detail"),
    path("anime/<slug:slug>/", views.AnimeDetailView.as_view(), name="anime-detail"),
    path("anime/<slug:slug>/rate/", views.rate_anime_view, name="anime-rate"),
    path("anime/<slug:slug>/watchlist/", views.toggle_watchlist_view, name="anime-watchlist-toggle"),
    path("anime/<slug:slug>/episode/<int:number>/", views.EpisodeDetailView.as_view(), name="episode-detail"),
    path("comments/<int:pk>/delete/", views.delete_comment_view, name="comment-delete"),
]
