from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.shop import selectors, services
from apps.shop.models import CartItem

from .factories import make_product, make_user


class CartServiceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.product = make_product(price="500.00", stock=5)

    def test_adding_the_same_product_twice_increments_one_line(self):
        services.add_to_cart(user=self.user, product=self.product, quantity=1)
        services.add_to_cart(user=self.user, product=self.product, quantity=2)

        items = CartItem.objects.filter(cart__user=self.user)
        self.assertEqual(items.count(), 1)
        self.assertEqual(items.first().quantity, 3)

    def test_a_user_only_ever_has_one_cart(self):
        services.add_to_cart(user=self.user, product=self.product)
        services.add_to_cart(user=self.user, product=make_product(name="Hoodie"))
        self.assertEqual(self.user.cart.items.count(), 2)

    def test_you_cannot_add_more_than_the_stock_on_hand(self):
        with self.assertRaises(ValidationError):
            services.add_to_cart(user=self.user, product=self.product, quantity=6)
        self.assertFalse(CartItem.objects.exists())

    def test_an_out_of_stock_product_cannot_be_added(self):
        sold_out = make_product(name="Sold out", stock=0)
        with self.assertRaises(ValidationError):
            services.add_to_cart(user=self.user, product=sold_out)

    def test_an_inactive_product_cannot_be_added(self):
        hidden = make_product(name="Hidden", is_active=False)
        with self.assertRaises(ValidationError):
            services.add_to_cart(user=self.user, product=hidden)

    @override_settings(CART_MAX_QUANTITY_PER_ITEM=3)
    def test_the_per_item_cap_is_enforced(self):
        with self.assertRaises(ValidationError):
            services.add_to_cart(user=self.user, product=self.product, quantity=4)

    def test_setting_a_quantity_of_zero_removes_the_line(self):
        services.add_to_cart(user=self.user, product=self.product, quantity=2)
        self.assertIsNone(services.set_cart_quantity(user=self.user, product=self.product, quantity=0))
        self.assertFalse(CartItem.objects.exists())

    def test_a_negative_quantity_is_rejected(self):
        with self.assertRaises(ValidationError):
            services.set_cart_quantity(user=self.user, product=self.product, quantity=-1)

    def test_the_subtotal_multiplies_price_by_quantity(self):
        services.add_to_cart(user=self.user, product=self.product, quantity=3)
        self.assertEqual(self.user.cart.subtotal, Decimal("1500.00"))
        self.assertEqual(self.user.cart.items_count, 3)


class CartSummaryTests(TestCase):
    def test_an_anonymous_visitor_gets_an_empty_summary_and_no_cart_row(self):
        """The old helper raised UnboundLocalError for anonymous visitors."""
        from django.contrib.auth.models import AnonymousUser

        summary = selectors.cart_summary(AnonymousUser())
        self.assertEqual(summary, {"count": 0, "subtotal": 0})

    def test_reading_the_summary_never_creates_a_cart(self):
        user = make_user()
        selectors.cart_summary(user)
        self.assertFalse(hasattr(user, "cart") and user.cart.pk)


class CartViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.product = make_product(stock=5)
        self.url = f"/shop/cart/update/{self.product.slug}/"

    def test_the_cart_endpoint_rejects_get(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        response = self.client.post(self.url, {"op": "add", "quantity": 1})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.headers["Location"])

    def test_adding_over_stock_returns_400_with_a_readable_reason(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url, {"op": "add", "quantity": 99}, HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertIn("Only 5 left", response.json()["message"])

    def test_an_unknown_action_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url, {"op": "sudo-free"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(response.status_code, 400)

    def test_a_successful_add_returns_the_new_badge_count(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url, {"op": "add", "quantity": 2}, HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 2)


class CartPayloadTests(TestCase):
    """The cart page patches itself from this payload instead of reloading."""

    def setUp(self):
        self.user = make_user()
        self.product = make_product(price="500.00", stock=10)
        self.client.force_login(self.user)
        self.url = f"/shop/cart/update/{self.product.slug}/"

    def test_the_response_carries_per_line_totals_and_the_currency(self):
        response = self.client.post(
            self.url, {"op": "add", "quantity": 3}, HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        payload = response.json()
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["subtotal"], "1500.00")
        self.assertEqual(payload["currency"], "৳")
        self.assertEqual(payload["lines"][self.product.slug]["quantity"], 3)
        self.assertEqual(payload["lines"][self.product.slug]["line_total"], "1500.00")

    def test_removing_the_last_line_leaves_no_lines(self):
        self.client.post(self.url, {"op": "add", "quantity": 1})
        response = self.client.post(
            self.url, {"op": "delete"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(response.json()["lines"], {})
        self.assertEqual(response.json()["count"], 0)


class FormFieldNamingTests(TestCase):
    """Guards the DOM collision that made every add-to-cart POST to a 404.

    A form control named ``action`` becomes a property of the form element and
    shadows ``HTMLFormElement.action``, so ``form.action`` in JavaScript returns
    the input instead of the URL.
    """

    SHADOWING_NAMES = {
        "action", "method", "target", "elements", "length",
        "submit", "reset", "enctype", "acceptCharset", "noValidate",
    }

    def test_no_cart_field_shadows_a_form_element_property(self):
        from apps.shop.forms import CartUpdateForm

        collisions = self.SHADOWING_NAMES & set(CartUpdateForm().fields)
        self.assertEqual(collisions, set(), f"These field names shadow form properties: {collisions}")

    def test_the_rendered_cart_form_posts_the_operation_as_op(self):
        user = make_user()
        product = make_product()
        self.client.force_login(user)
        response = self.client.get(product.get_absolute_url())
        self.assertContains(response, 'name="op"')
        self.assertNotContains(response, 'name="action"')
