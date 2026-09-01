"""Event views. Registration is POST-only and idempotent."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, View

from . import selectors, services
from .forms import RegistrationForm
from .models import Event


class EventListView(TemplateView):
    template_name = "events/event_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["upcoming"] = selectors.upcoming_events(user=self.request.user)
        context["past"] = selectors.past_events(user=self.request.user)
        return context


class EventDetailView(View):
    def get(self, request, slug: str):
        event = selectors.event_detail(slug=slug)
        registration = selectors.registration_for(request.user, event)
        initial = {}
        if request.user.is_authenticated:
            initial = {"contact_email": request.user.email, "contact_phone": request.user.phone}
        context = {
            "event": event,
            "registration": registration,
            "form": RegistrationForm(initial=initial),
        }
        return render(request, "events/event_detail.html", context)


class RegisterView(LoginRequiredMixin, View):
    """POST only.

    The previous build created a registration row on a plain GET, so every
    crawler and every browser prefetch signed the user up again.
    """

    def get(self, request, slug: str):
        return redirect("events:detail", slug=slug)

    def post(self, request, slug: str):
        event = selectors.event_detail(slug=slug)
        form = RegistrationForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Check your contact details and try again.")
            return render(
                request,
                "events/event_detail.html",
                {"event": event, "registration": None, "form": form},
                status=400,
            )

        try:
            services.register_for_event(
                user=request.user,
                event=event,
                contact_email=form.cleaned_data["contact_email"],
                contact_phone=form.cleaned_data["contact_phone"],
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect(event)

        messages.success(request, f"You are registered for {event.title}.")
        return redirect("events:ticket", slug=event.slug)


class TicketView(LoginRequiredMixin, View):
    """Confirmation page, readable only by the person who holds the seat."""

    def get(self, request, slug: str):
        event = selectors.event_detail(slug=slug)
        registration = selectors.registration_for(request.user, event)
        if registration is None:
            messages.info(request, "You are not registered for this event yet.")
            return redirect(event)
        return render(request, "events/ticket.html", {"event": event, "registration": registration})


class MyRegistrationsView(LoginRequiredMixin, TemplateView):
    template_name = "events/my_registrations.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["registrations"] = selectors.registrations_for_user(self.request.user)
        return context


@login_required
@require_POST
def cancel_registration_view(request, slug: str):
    event = get_object_or_404(Event, slug=slug)
    try:
        cancelled = services.cancel_registration(user=request.user, event=event)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(
            request,
            "Registration cancelled." if cancelled else "You were not registered for this event.",
        )
    return redirect("events:my-registrations")
