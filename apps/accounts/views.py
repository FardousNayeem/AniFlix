"""Profile views. Thin: they fetch, they render, they delegate."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import TemplateView, UpdateView

from apps.events.selectors import registrations_for_user
from apps.shop.selectors import recent_orders_for_user
from apps.streaming.selectors import watchlist_for_user

from .forms import ProfileForm
from .models import User


class ProfileView(LoginRequiredMixin, TemplateView):
    """A user's own profile.

    The old ``/profile/<pk>`` route let anyone read any account's phone number
    and address by guessing an id. Profiles are now self-only.
    """

    template_name = "accounts/profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["profile_user"] = user
        context["watchlist_count"] = watchlist_for_user(user).count()
        context["event_count"] = registrations_for_user(user).count()
        context["order_count"] = recent_orders_for_user(user).count()
        return context


class ProfileEditView(LoginRequiredMixin, UpdateView):
    model = User
    form_class = ProfileForm
    template_name = "accounts/profile_edit.html"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None) -> User:
        return self.request.user

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Profile updated.")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "Check the highlighted fields and try again.")
        return super().form_invalid(form)


class WatchlistView(LoginRequiredMixin, TemplateView):
    """Bookmarked anime, previously called 'favorites'."""

    template_name = "accounts/watchlist.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["animes"] = watchlist_for_user(self.request.user)
        return context


class ActivityView(LoginRequiredMixin, TemplateView):
    """Everything the account is signed up for, in one place."""

    template_name = "accounts/activity.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["registrations"] = registrations_for_user(user)
        context["orders"] = recent_orders_for_user(user)[:5]
        return context
