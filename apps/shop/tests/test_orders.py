from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.shop import services
from apps.shop.models import Order, Product

from .factories import SHIPPING, make_product, make_user


class PlaceOrderTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.product = make_product(price="500.00", stock=10)

    def test_placing_an_order_snapshots_the_price_and_empties_the_cart(self):
        services.add_to_cart(user=self.user, product=self.product, quantity=2)
        order = services.place_order(user=self.user, shipping=SHIPPING)

        self.assertEqual(order.total, Decimal("1000.00"))
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.items.count(), 1)
        self.assertTrue(self.user.cart.is_empty)

    def test_changing_a_price_later_does_not_rewrite_the_receipt(self):
        """The old schema recomputed an order's total from live product prices."""
        services.add_to_cart(user=self.user, product=self.product, quantity=2)
        order = services.place_order(user=self.user, shipping=SHIPPING)

        self.product.price = Decimal("9999.00")
        self.product.save(update_fields=["price"])

        order.refresh_from_db()
        self.assertEqual(order.total, Decimal("1000.00"))
        self.assertEqual(order.items.first().unit_price, Decimal("500.00"))

    def test_deleting_a_product_later_does_not_break_the_receipt(self):
        services.add_to_cart(user=self.user, product=self.product, quantity=1)
        order = services.place_order(user=self.user, shipping=SHIPPING)
        Product.objects.filter(pk=self.product.pk).delete()

        item = order.items.first()
        self.assertIsNone(item.product)
        self.assertEqual(item.product_name, "Berserk Black T-Shirt")
        self.assertEqual(item.line_total, Decimal("500.00"))

    def test_an_empty_cart_cannot_be_checked_out(self):
        with self.assertRaises(ValidationError):
            services.place_order(user=self.user, shipping=SHIPPING)

    def test_stock_is_rechecked_at_checkout(self):
        services.add_to_cart(user=self.user, product=self.product, quantity=5)
        Product.objects.filter(pk=self.product.pk).update(stock=1)

        with self.assertRaises(ValidationError):
            services.place_order(user=self.user, shipping=SHIPPING)
        self.assertFalse(Order.objects.exists())

    def test_a_product_deactivated_before_checkout_blocks_the_order(self):
        services.add_to_cart(user=self.user, product=self.product, quantity=1)
        Product.objects.filter(pk=self.product.pk).update(is_active=False)

        with self.assertRaises(ValidationError):
            services.place_order(user=self.user, shipping=SHIPPING)

    def test_order_references_are_unique(self):
        references = set()
        for index in range(5):
            services.add_to_cart(user=self.user, product=self.product, quantity=1)
            references.add(services.place_order(user=self.user, shipping=SHIPPING).reference)
        self.assertEqual(len(references), 5)


class OrderSettlementTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.product = make_product(price="500.00", stock=10)
        services.add_to_cart(user=self.user, product=self.product, quantity=2)
        self.order = services.place_order(user=self.user, shipping=SHIPPING)

    def test_marking_paid_records_the_transaction_and_reduces_stock(self):
        services.mark_order_paid(order=self.order, transaction_id="TXN-1", validation_id="VAL-1")

        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.transaction_id, "TXN-1")
        self.assertIsNotNone(self.order.paid_at)
        self.assertEqual(self.product.stock, 8)

    def test_replaying_a_gateway_callback_does_not_deduct_stock_twice(self):
        """Gateways retry. Settlement has to be idempotent."""
        services.mark_order_paid(order=self.order, transaction_id="TXN-1")
        services.mark_order_paid(order=self.order, transaction_id="TXN-1")
        services.mark_order_paid(order=self.order, transaction_id="TXN-1")

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)

    def test_a_failed_payment_leaves_stock_alone(self):
        services.mark_order_failed(order=self.order, reason="declined")
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FAILED)
        self.assertEqual(self.product.stock, 10)

    def test_a_paid_order_cannot_be_flipped_back_to_failed(self):
        services.mark_order_paid(order=self.order, transaction_id="TXN-1")
        services.mark_order_failed(order=self.order, reason="late callback")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_only_the_owner_can_cancel(self):
        stranger = make_user("stranger@example.com")
        with self.assertRaises(PermissionError):
            services.cancel_order(user=stranger, order=self.order)

    def test_a_paid_order_cannot_be_cancelled(self):
        services.mark_order_paid(order=self.order, transaction_id="TXN-1")
        with self.assertRaises(ValidationError):
            services.cancel_order(user=self.user, order=self.order)


class OrderAccessTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.stranger = make_user("stranger@example.com")
        product = make_product()
        services.add_to_cart(user=self.owner, product=product, quantity=1)
        self.order = services.place_order(user=self.owner, shipping=SHIPPING)

    def test_the_owner_can_read_their_receipt(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.order.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.order.reference)

    def test_another_user_gets_404_not_somebody_elses_address(self):
        """The old receipt view looked orders up by id with no ownership check."""
        self.client.force_login(self.stranger)
        response = self.client.get(self.order.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_the_order_list_only_shows_your_own_orders(self):
        self.client.force_login(self.stranger)
        response = self.client.get("/shop/orders/")
        self.assertEqual(list(response.context["orders"]), [])

    def test_cancelling_someone_elses_order_is_refused(self):
        self.client.force_login(self.stranger)
        response = self.client.post(f"/shop/orders/{self.order.reference}/cancel/")
        self.assertEqual(response.status_code, 404)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)


class CheckoutViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.product = make_product(stock=5)

    def test_checkout_with_an_empty_cart_redirects_to_the_shop(self):
        self.client.force_login(self.user)
        response = self.client.get("/shop/checkout/")
        self.assertRedirects(response, "/shop/")

    def test_checkout_prefills_the_saved_address(self):
        services.add_to_cart(user=self.user, product=self.product, quantity=1)
        self.client.force_login(self.user)
        response = self.client.get("/shop/checkout/")
        self.assertEqual(response.context["form"].initial["city"], "Dhaka")

    def test_an_invalid_phone_number_blocks_the_order(self):
        services.add_to_cart(user=self.user, product=self.product, quantity=1)
        self.client.force_login(self.user)
        payload = dict(SHIPPING, phone="not-a-phone")
        response = self.client.post("/shop/checkout/", payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_a_valid_checkout_creates_a_pending_order_and_goes_to_payment(self):
        services.add_to_cart(user=self.user, product=self.product, quantity=1)
        self.client.force_login(self.user)
        response = self.client.post("/shop/checkout/", SHIPPING)

        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertRedirects(response, f"/shop/pay/{order.reference}/", target_status_code=302)
