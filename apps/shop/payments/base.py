"""Payment gateway contract.

Views depend on this interface, never on a vendor SDK, so the sandbox gateway
can be swapped for SSLCommerz (or anything else) by changing one setting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from apps.shop.models import Order


class PaymentError(Exception):
    """Raised when a gateway cannot be reached or rejects a request."""


@dataclass(frozen=True)
class CheckoutSession:
    """Where to send the customer to pay."""

    redirect_url: str
    transaction_id: str


@dataclass(frozen=True)
class PaymentResult:
    """What a gateway callback actually proved."""

    is_successful: bool
    transaction_id: str = ""
    validation_id: str = ""
    amount: str = ""
    reason: str = ""
    raw: dict = field(default_factory=dict)


class PaymentGateway(Protocol):
    name: str

    def create_session(
        self, *, order: Order, success_url: str, fail_url: str, cancel_url: str, ipn_url: str
    ) -> CheckoutSession:
        """Open a payment session and return where to send the customer."""

    def verify(self, *, order: Order, payload: dict) -> PaymentResult:
        """Confirm a callback really represents a settled payment for ``order``."""
