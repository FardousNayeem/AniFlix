from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.events import selectors, services
from apps.events.models import Event, Registration, Venue


def make_user(email="fan@example.com") -> User:
    return User.objects.create_user(
        email=email, password="test-pass-123", display_name="Nadia Karim", phone="+8801711223344"
    )


def make_event(title="JiliCon", days_ahead=14, capacity=0, **kwargs) -> Event:
    venue = kwargs.pop("venue", None) or Venue.objects.create(
        name="Dhaka Convention Centre", address="Gulshan 2", city="Dhaka"
    )
    return Event.objects.create(
        title=title,
        description="A day of screenings, panels and cosplay.",
        organiser_email="hello@jilicon.test",
        starts_at=timezone.now() + timedelta(days=days_ahead),
        venue=venue,
        capacity=capacity,
        **kwargs,
    )


class EventModelTests(TestCase):
    def test_slugs_are_generated_and_stay_unique(self):
        first = make_event("Anime Night")
        second = make_event("Anime Night")
        self.assertEqual(first.slug, "anime-night")
        self.assertEqual(second.slug, "anime-night-2")

    def test_zero_capacity_means_unlimited(self):
        event = make_event(capacity=0)
        self.assertIsNone(event.seats_left)
        self.assertFalse(event.is_full)

    def test_seats_left_counts_down(self):
        event = make_event(capacity=2)
        services.register_for_event(user=make_user("a@example.com"), event=event, contact_email="a@example.com")
        self.assertEqual(event.seats_left, 1)
        self.assertFalse(event.is_full)

        services.register_for_event(user=make_user("b@example.com"), event=event, contact_email="b@example.com")
        self.assertEqual(event.seats_left, 0)
        self.assertTrue(event.is_full)


class RegistrationServiceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.event = make_event()

    def test_registering_issues_a_unique_reference(self):
        first = services.register_for_event(user=self.user, event=self.event, contact_email=self.user.email)
        second_event = make_event("Another Con")
        second = services.register_for_event(user=self.user, event=second_event, contact_email=self.user.email)

        self.assertTrue(first.reference)
        self.assertNotEqual(first.reference, second.reference)

    def test_registering_twice_is_refused_rather_than_duplicated(self):
        """The old GET handler created a new row on every page load."""
        services.register_for_event(user=self.user, event=self.event, contact_email=self.user.email)
        with self.assertRaises(ValidationError):
            services.register_for_event(user=self.user, event=self.event, contact_email=self.user.email)
        self.assertEqual(Registration.objects.count(), 1)

    def test_a_full_event_cannot_be_oversold(self):
        event = make_event(capacity=1)
        services.register_for_event(user=self.user, event=event, contact_email=self.user.email)
        with self.assertRaises(ValidationError):
            services.register_for_event(
                user=make_user("late@example.com"), event=event, contact_email="late@example.com"
            )

    def test_a_past_event_cannot_be_joined(self):
        past = make_event("Last year", days_ahead=-30)
        with self.assertRaises(ValidationError):
            services.register_for_event(user=self.user, event=past, contact_email=self.user.email)

    def test_an_unpublished_event_cannot_be_joined(self):
        hidden = make_event("Draft", is_published=False)
        with self.assertRaises(ValidationError):
            services.register_for_event(user=self.user, event=hidden, contact_email=self.user.email)

    def test_cancelling_frees_the_seat(self):
        event = make_event(capacity=1)
        services.register_for_event(user=self.user, event=event, contact_email=self.user.email)
        self.assertTrue(services.cancel_registration(user=self.user, event=event))
        self.assertFalse(event.is_full)

    def test_cancelling_when_not_registered_reports_false(self):
        self.assertFalse(services.cancel_registration(user=self.user, event=self.event))

    def test_a_past_event_cannot_be_cancelled(self):
        past = make_event("Last year", days_ahead=-30)
        Registration.objects.create(user=self.user, event=past, contact_email=self.user.email)
        with self.assertRaises(ValidationError):
            services.cancel_registration(user=self.user, event=past)


class EventViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.event = make_event()
        self.register_url = f"/events/{self.event.slug}/register/"

    def test_a_get_to_the_register_url_does_not_sign_anyone_up(self):
        """This was the single worst bug in the events feature."""
        self.client.force_login(self.user)
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Registration.objects.exists())

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        response = self.client.post(self.register_url, {"contact_email": "a@example.com"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.headers["Location"])

    def test_a_valid_post_registers_and_shows_the_ticket(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.register_url, {"contact_email": self.user.email, "contact_phone": "+8801711223344"}
        )
        self.assertRedirects(response, f"/events/{self.event.slug}/ticket/")
        self.assertEqual(Registration.objects.count(), 1)

    def test_an_invalid_email_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(self.register_url, {"contact_email": "not-an-email"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Registration.objects.exists())

    def test_a_ticket_is_only_visible_to_the_person_holding_it(self):
        services.register_for_event(user=self.user, event=self.event, contact_email=self.user.email)
        stranger = make_user("stranger@example.com")

        self.client.force_login(stranger)
        response = self.client.get(f"/events/{self.event.slug}/ticket/")
        self.assertRedirects(response, self.event.get_absolute_url())

    def test_an_unpublished_event_is_not_reachable(self):
        hidden = make_event("Draft", is_published=False)
        self.assertEqual(self.client.get(hidden.get_absolute_url()).status_code, 404)

    def test_my_registrations_only_lists_your_own(self):
        services.register_for_event(user=self.user, event=self.event, contact_email=self.user.email)
        stranger = make_user("stranger@example.com")

        self.client.force_login(stranger)
        response = self.client.get("/events/mine/")
        self.assertEqual(list(response.context["registrations"]), [])


class EventSelectorTests(TestCase):
    def test_upcoming_and_past_are_split_by_the_clock(self):
        upcoming = make_event("Soon", days_ahead=5)
        past = make_event("Done", days_ahead=-5)

        self.assertIn(upcoming, selectors.upcoming_events())
        self.assertNotIn(past, selectors.upcoming_events())
        self.assertIn(past, selectors.past_events())

    def test_the_list_page_runs_a_constant_number_of_queries(self):
        user = make_user()
        for index in range(10):
            make_event(f"Con {index}")

        with self.assertNumQueries(1):
            list(selectors.upcoming_events(user=user))
