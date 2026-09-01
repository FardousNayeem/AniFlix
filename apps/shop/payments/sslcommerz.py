"""SSLCommerz gateway.

Two things the previous implementation got wrong and this one does not:

1. It trusted the browser. A ``POST`` to the success URL with
   ``status=VALID`` was enough to mark an order paid, without ever asking
   SSLCommerz whether the payment happened. Every callback is now validated
   server side against the gateway's validation API.
2. It never compared the amount. A customer could pay ৳1 for a ৳5,000 order.
   The settled amount is now checked against the order total.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings

from apps.shop.models import Order

from .base import CheckoutSession, PaymentError, PaymentResult

logger = logging.getLogger(__name__)

SANDBOX_VALIDATION_URL = "https://sandbox.sslcommerz.com/validator/api/validationserverAPI.php"
LIVE_VALIDATION_URL = "https://securepay.sslcommerz.com/validator/api/validationserverAPI.php"
REQUEST_TIMEOUT_SECONDS = 20


class SSLCommerzGateway:
    name = "sslcommerz"

    def __init__(self) -> None:
        self.store_id = settings.SSLCOMMERZ_STORE_ID
        self.store_password = settings.SSLCOMMERZ_STORE_PASSWORD
        self.is_sandbox = settings.SSLCOMMERZ_SANDBOX
        if not self.store_id or not self.store_password:
            raise PaymentError(
                "SSLCommerz credentials are missing. Set SSLCOMMERZ_STORE_ID and "
                "SSLCOMMERZ_STORE_PASSWORD, or set PAYMENT_GATEWAY=dummy for local work."
            )

    def create_session(
        self, *, order: Order, success_url: str, fail_url: str, cancel_url: str, ipn_url: str
    ) -> CheckoutSession:
        from sslcommerz_lib import SSLCOMMERZ

        client = SSLCOMMERZ(
            {
                "store_id": self.store_id,
                "store_pass": self.store_password,
                "issandbox": self.is_sandbox,
            }
        )
        payload = {
            "total_amount": str(order.total),
            "currency": settings.CURRENCY_CODE,
            "tran_id": order.reference,
            "success_url": success_url,
            "fail_url": fail_url,
            "cancel_url": cancel_url,
            "ipn_url": ipn_url,
            "emi_option": 0,
            # Real customer data, not the hardcoded placeholders the old code sent.
            "cus_name": order.ship_to_name,
            "cus_email": order.user.email,
            "cus_phone": order.ship_to_phone,
            "cus_add1": order.ship_to_address,
            "cus_city": order.ship_to_city,
            "cus_postcode": order.ship_to_postcode or "0000",
            "cus_country": "Bangladesh",
            "shipping_method": "Courier",
            "ship_name": order.ship_to_name,
            "ship_add1": order.ship_to_address,
            "ship_city": order.ship_to_city,
            "ship_postcode": order.ship_to_postcode or "0000",
            "ship_country": "Bangladesh",
            "num_of_item": order.items_count,
            "product_name": ", ".join(item.product_name for item in order.items.all())[:255],
            "product_category": "Merchandise",
            "product_profile": "physical-goods",
        }

        try:
            response = client.createSession(payload)
        except Exception as exc:  # noqa: BLE001 - vendor SDK raises bare exceptions
            logger.exception("SSLCommerz session creation failed for order %s", order.reference)
            raise PaymentError("Could not reach the payment gateway. Try again.") from exc

        if response.get("status") != "SUCCESS" or not response.get("GatewayPageURL"):
            reason = response.get("failedreason") or "Gateway refused the session."
            logger.error("SSLCommerz refused order %s: %s", order.reference, reason)
            raise PaymentError(reason)

        return CheckoutSession(
            redirect_url=response["GatewayPageURL"], transaction_id=order.reference
        )

    def verify(self, *, order: Order, payload: dict) -> PaymentResult:
        validation_id = payload.get("val_id", "")
        if not validation_id:
            return PaymentResult(is_successful=False, reason="Callback carried no validation id.", raw=payload)

        url = SANDBOX_VALIDATION_URL if self.is_sandbox else LIVE_VALIDATION_URL
        try:
            response = requests.get(
                url,
                params={
                    "val_id": validation_id,
                    "store_id": self.store_id,
                    "store_passwd": self.store_password,
                    "format": "json",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.exception("SSLCommerz validation call failed for order %s", order.reference)
            raise PaymentError("Could not confirm the payment with the gateway.") from exc

        status = data.get("status")
        if status not in {"VALID", "VALIDATED"}:
            return PaymentResult(
                is_successful=False,
                reason=data.get("error") or f"Gateway reported status {status!r}.",
                raw=data,
            )

        if data.get("tran_id") != order.reference:
            logger.error(
                "SSLCommerz validation for %s referenced a different order (%s)",
                order.reference,
                data.get("tran_id"),
            )
            return PaymentResult(is_successful=False, reason="Payment does not match this order.", raw=data)

        try:
            paid_amount = Decimal(str(data.get("amount", "0")))
        except (InvalidOperation, TypeError):
            return PaymentResult(is_successful=False, reason="Gateway returned an unreadable amount.", raw=data)

        if paid_amount < order.total:
            logger.error(
                "Underpayment on order %s: paid %s of %s", order.reference, paid_amount, order.total
            )
            return PaymentResult(is_successful=False, reason="The amount paid does not cover this order.", raw=data)

        return PaymentResult(
            is_successful=True,
            transaction_id=data.get("tran_id", order.reference),
            validation_id=validation_id,
            amount=str(paid_amount),
            raw=data,
        )
