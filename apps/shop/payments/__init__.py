"""Gateway selection.

``settings.PAYMENT_GATEWAY`` decides which implementation the checkout uses.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import CheckoutSession, PaymentError, PaymentGateway, PaymentResult
from .dummy import DummyGateway

__all__ = [
    "CheckoutSession",
    "PaymentError",
    "PaymentGateway",
    "PaymentResult",
    "get_gateway",
]


def get_gateway() -> PaymentGateway:
    name = (settings.PAYMENT_GATEWAY or "dummy").lower()
    if name == "dummy":
        return DummyGateway()
    if name == "sslcommerz":
        from .sslcommerz import SSLCommerzGateway

        return SSLCommerzGateway()
    raise ImproperlyConfigured(
        f"Unknown PAYMENT_GATEWAY {name!r}. Use 'dummy' or 'sslcommerz'."
    )
