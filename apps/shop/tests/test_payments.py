"""Payment flow tests.

These cover the two holes in the previous implementation: the callback was
never verified with the gateway, and the settled amount was never compared to
the order total.
"""

from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings

from apps.shop import services
from apps.shop.models import Order
from apps.shop.payments import PaymentError, get_gateway
from apps.shop.payments.base import PaymentResult

from .factories import SHIPPING, make_product, make_user


def build_order(user, price="500.00", quantity=2) -> Order:
    product = make_product(price=price, stock=10)
    services.add_to_cart(user=user, product=product, quantity=quantity)
    return services.place_order(user=user, shipping=SHIPPING)


class GatewaySelectionTests(TestCase):
    @override_settings(PAYMENT_GATEWAY="dummy")
    def test_the_dummy_gateway_is_the_default_for_local_work(self):
        self.assertEqual(get_gateway().name, "dummy")

    @override_settings(PAYMENT_GATEWAY="sslcommerz", SSLCOMMERZ_STORE_ID="", SSLCOMMERZ_STORE_PASSWORD="")
    def test_sslcommerz_refuses_to_start_without_credentials(self):
        """Credentials used to be hardcoded in the view; now their absence is loud."""
        with self.assertRaises(PaymentError):
            get_gateway()

    @override_settings(PAYMENT_GATEWAY="something-else")
    def test_an_unknown_gateway_name_is_a_configuration_error(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured):
            get_gateway()


class PaymentCallbackTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.order = build_order(self.user)

    def test_a_browser_cannot_mark_an_order_paid_by_asserting_it(self):
        """A POST of status=VALID used to be enough to settle an order."""
        forged = PaymentResult(is_successful=False, reason="Gateway said no.")
        with mock.patch("apps.shop.payments.dummy.DummyGateway.verify", return_value=forged):
            self.client.post(f"/shop/pay/{self.order.reference}/return/", {"status": "VALID"})

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FAILED)

    def test_a_verified_callback_settles_the_order(self):
        self.client.post(
            f"/shop/pay/{self.order.reference}/return/",
            {"status": "VALID", "tran_id": self.order.reference, "val_id": "VAL-9"},
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.validation_id, "VAL-9")

    def test_a_cancelled_callback_does_not_settle_the_order(self):
        self.client.post(f"/shop/pay/{self.order.reference}/return/", {"status": "CANCELLED"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FAILED)

    def test_a_gateway_outage_leaves_the_order_untouched(self):
        with mock.patch(
            "apps.shop.payments.dummy.DummyGateway.verify", side_effect=PaymentError("gateway down")
        ):
            self.client.post(f"/shop/pay/{self.order.reference}/return/", {"status": "VALID"})

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_the_ipn_endpoint_settles_the_order_without_a_browser(self):
        response = self.client.post(
            f"/shop/pay/{self.order.reference}/ipn/",
            {"status": "VALID", "tran_id": self.order.reference, "val_id": "VAL-9"},
        )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_the_ipn_endpoint_rejects_get(self):
        self.assertEqual(self.client.get(f"/shop/pay/{self.order.reference}/ipn/").status_code, 405)

    def test_a_callback_for_an_unknown_reference_is_a_404(self):
        response = self.client.post("/shop/pay/OF-NOPE/return/", {"status": "VALID"})
        self.assertEqual(response.status_code, 404)


class PaymentStartTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.order = build_order(self.owner)

    def test_a_stranger_cannot_start_payment_for_your_order(self):
        stranger = make_user("stranger@example.com")
        self.client.force_login(stranger)
        response = self.client.get(f"/shop/pay/{self.order.reference}/")
        self.assertEqual(response.status_code, 404)

    def test_starting_payment_redirects_to_the_gateway(self):
        self.client.force_login(self.owner)
        response = self.client.get(f"/shop/pay/{self.order.reference}/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/return/", response.headers["Location"])

    def test_an_already_paid_order_goes_straight_to_the_receipt(self):
        services.mark_order_paid(order=self.order, transaction_id="TXN-1")
        self.client.force_login(self.owner)
        response = self.client.get(f"/shop/pay/{self.order.reference}/")
        self.assertRedirects(response, self.order.get_absolute_url())


class SSLCommerzVerificationTests(TestCase):
    """The verification rules, exercised without touching the network."""

    def setUp(self):
        self.user = make_user()
        self.order = build_order(self.user, price="500.00", quantity=2)  # total 1000.00

    def _gateway(self):
        with override_settings(
            PAYMENT_GATEWAY="sslcommerz",
            SSLCOMMERZ_STORE_ID="store",
            SSLCOMMERZ_STORE_PASSWORD="pass",
            SSLCOMMERZ_SANDBOX=True,
        ):
            from apps.shop.payments.sslcommerz import SSLCommerzGateway

            return SSLCommerzGateway()

    def _verify(self, api_response):
        gateway = self._gateway()
        fake = mock.Mock()
        fake.json.return_value = api_response
        fake.raise_for_status.return_value = None
        with mock.patch("apps.shop.payments.sslcommerz.requests.get", return_value=fake):
            return gateway.verify(order=self.order, payload={"val_id": "VAL-1"})

    def test_a_matching_valid_payment_is_accepted(self):
        result = self._verify(
            {"status": "VALID", "tran_id": self.order.reference, "amount": "1000.00", "val_id": "VAL-1"}
        )
        self.assertTrue(result.is_successful)

    def test_underpayment_is_rejected(self):
        """Paying 1 BDT for a 1000 BDT order used to settle the order."""
        result = self._verify({"status": "VALID", "tran_id": self.order.reference, "amount": "1.00"})
        self.assertFalse(result.is_successful)
        self.assertIn("does not cover", result.reason)

    def test_a_payment_for_a_different_order_is_rejected(self):
        result = self._verify({"status": "VALID", "tran_id": "OF-SOMEONEELSE", "amount": "1000.00"})
        self.assertFalse(result.is_successful)

    def test_a_non_valid_status_is_rejected(self):
        result = self._verify({"status": "FAILED", "tran_id": self.order.reference, "amount": "1000.00"})
        self.assertFalse(result.is_successful)

    def test_a_callback_with_no_validation_id_never_reaches_the_network(self):
        gateway = self._gateway()
        with mock.patch("apps.shop.payments.sslcommerz.requests.get") as http:
            result = gateway.verify(order=self.order, payload={"status": "VALID"})
        http.assert_not_called()
        self.assertFalse(result.is_successful)

    def test_an_unreadable_amount_is_rejected_rather_than_crashing(self):
        result = self._verify({"status": "VALID", "tran_id": self.order.reference, "amount": "one thousand"})
        self.assertFalse(result.is_successful)

    def test_overpayment_is_accepted(self):
        result = self._verify(
            {"status": "VALIDATED", "tran_id": self.order.reference, "amount": "1200.00"}
        )
        self.assertTrue(result.is_successful)
        self.assertEqual(Decimal(result.amount), Decimal("1200.00"))
