"""Development gateway.

Skips the network entirely and bounces straight back to the success URL, so
the checkout flow can be exercised (and tested) without live credentials.
"""

from __future__ import annotations

import uuid

from apps.shop.models import Order

from .base import CheckoutSession, PaymentResult


class DummyGateway:
    name = "dummy"

    def create_session(
        self, *, order: Order, success_url: str, fail_url: str, cancel_url: str, ipn_url: str
    ) -> CheckoutSession:
        transaction_id = f"DEV-{uuid.uuid4().hex[:12].upper()}"
        separator = "&" if "?" in success_url else "?"
        return CheckoutSession(
            redirect_url=f"{success_url}{separator}tran_id={transaction_id}&status=VALID",
            transaction_id=transaction_id,
        )

    def verify(self, *, order: Order, payload: dict) -> PaymentResult:
        if payload.get("status") != "VALID":
            return PaymentResult(is_successful=False, reason="Payment was not completed.", raw=payload)
        return PaymentResult(
            is_successful=True,
            transaction_id=payload.get("tran_id", ""),
            validation_id=payload.get("val_id", "SANDBOX"),
            amount=str(order.total),
            raw=payload,
        )
