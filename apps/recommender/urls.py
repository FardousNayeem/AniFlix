from django.urls import path

from . import views

app_name = "recommender"

urlpatterns = [
    path("questions/", views.questions_view, name="questions"),
    path("ask/", views.recommend_view, name="ask"),
]
