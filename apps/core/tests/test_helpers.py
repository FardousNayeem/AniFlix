from django.test import RequestFactory, SimpleTestCase

from apps.core.pagination import querystring_without_page
from apps.core.templatetags.ui import initials, money


class MoneyFilterTests(SimpleTestCase):
    def test_formats_with_currency_symbol_and_thousands(self):
        self.assertEqual(money(1250), "৳1,250.00")

    def test_non_numeric_values_fall_back_to_zero(self):
        self.assertEqual(money(None), "৳0.00")
        self.assertEqual(money("not a number"), "৳0.00")


class InitialsFilterTests(SimpleTestCase):
    def test_uses_first_letter_of_each_word(self):
        self.assertEqual(initials("Farhan Rahman"), "FR")

    def test_falls_back_to_the_email_local_part(self):
        self.assertEqual(initials("rumi.ahsan@example.com"), "RA")

    def test_single_word_uses_first_two_letters(self):
        self.assertEqual(initials("Nadia"), "NA")

    def test_blank_input_is_safe(self):
        self.assertEqual(initials(""), "?")
        self.assertEqual(initials(None), "?")


class QuerystringTests(SimpleTestCase):
    def test_drops_page_but_keeps_other_filters(self):
        request = RequestFactory().get("/browse/", {"q": "gintama", "sort": "rating", "page": "3"})
        result = querystring_without_page(request)
        self.assertIn("q=gintama", result)
        self.assertIn("sort=rating", result)
        self.assertNotIn("page", result)
        self.assertTrue(result.startswith("&"))

    def test_empty_querystring_returns_empty_string(self):
        request = RequestFactory().get("/browse/")
        self.assertEqual(querystring_without_page(request), "")
