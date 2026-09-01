from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.ProfileView.as_view(), name="profile"),
    path("edit/", views.ProfileEditView.as_view(), name="profile-edit"),
    path("my-list/", views.WatchlistView.as_view(), name="watchlist"),
    path("activity/", views.ActivityView.as_view(), name="activity"),
]
