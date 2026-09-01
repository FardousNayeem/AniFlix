from django.db.utils import IntegrityError
from django.test import TestCase

from apps.accounts.models import User


class UserModelTests(TestCase):
    def test_email_is_lowercased_and_backfills_username(self):
        user = User.objects.create_user(email="Rumi.Ahsan@Example.COM", password="pw")
        self.assertEqual(user.email, "rumi.ahsan@example.com")
        self.assertEqual(user.username, "rumi.ahsan@example.com")

    def test_email_must_be_unique(self):
        User.objects.create_user(email="dup@example.com", password="pw")
        with self.assertRaises(IntegrityError):
            User.objects.create_user(email="dup@example.com", password="pw")

    def test_creating_a_user_without_an_email_is_rejected(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="pw")

    def test_public_name_prefers_display_name(self):
        user = User.objects.create_user(email="nadia@example.com", password="pw", display_name="Nadia")
        self.assertEqual(user.public_name, "Nadia")

    def test_public_name_falls_back_to_the_email_local_part(self):
        user = User.objects.create_user(email="nadia@example.com", password="pw")
        self.assertEqual(user.public_name, "nadia")

    def test_has_shipping_details_requires_all_three_fields(self):
        user = User.objects.create_user(email="ship@example.com", password="pw")
        self.assertFalse(user.has_shipping_details)
        user.address, user.city = "12 Green Road", "Dhaka"
        self.assertFalse(user.has_shipping_details)
        user.phone = "+8801711223344"
        self.assertTrue(user.has_shipping_details)


class ProfileAccessTests(TestCase):
    def test_a_user_only_ever_sees_their_own_profile(self):
        """The old /profile/<pk> route exposed any account's phone and address."""
        User.objects.create_user(email="victim@example.com", password="pw", phone="+8801711223344")
        snooper = User.objects.create_user(email="snooper@example.com", password="pw")

        self.client.force_login(snooper)
        response = self.client.get("/profile/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["profile_user"], snooper)
        self.assertNotContains(response, "+8801711223344")
