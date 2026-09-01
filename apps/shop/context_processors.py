"""Cart badge data for the navbar.

Replaces the previous context processor, which imported a model that did not
exist (``Order_Product``) and would have raised on every request had it ever
been wired into TEMPLATES.
"""

from . import selectors


def cart_summary(request) -> dict:
    return {"cart": selectors.cart_summary(request.user)}
