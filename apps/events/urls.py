from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.EventListView.as_view(), name="list"),
    path("mine/", views.MyRegistrationsView.as_view(), name="my-registrations"),
    path("<slug:slug>/", views.EventDetailView.as_view(), name="detail"),
    path("<slug:slug>/register/", views.RegisterView.as_view(), name="register"),
    path("<slug:slug>/cancel/", views.cancel_registration_view, name="cancel"),
    path("<slug:slug>/ticket/", views.TicketView.as_view(), name="ticket"),
]
