"""Write-side operations for the store.

Cart mutation, checkout and payment reconciliation all live here so that the
same invariants (stock, quantity caps, ownership, idempotency) hold no matter
which view is calling.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Cart, CartItem, Order, OrderItem, Product

logger = logging.getLogger(__name__)


def get_or_create_cart(user) -> Cart:
    cart, _created = Cart.objects.get_or_create(user=user)
    return cart


def _validate_quantity(product: Product, quantity: int) -> int:
    """Enforce the tighter of the two limits and say which one bit.

    Stock is checked before the per-item cap so a customer asking for 99 of a
    5-in-stock item is told there are 5 left, not that the cap is 10.
    """
    if quantity > product.stock:
        raise ValidationError(
            f"Only {product.stock} left in stock." if product.stock else "This item is out of stock."
        )
    cap = settings.CART_MAX_QUANTITY_PER_ITEM
    if quantity > cap:
        raise ValidationError(f"You can order at most {cap} of one item.")
    return quantity


def add_to_cart(*, user, product: Product, quantity: int = 1) -> CartItem:
    if quantity < 1:
        raise ValidationError("Quantity must be at least 1.")
    if not product.in_stock:
        raise ValidationError("This item is out of stock.")

    with transaction.atomic():
        cart = get_or_create_cart(user)
        item, created = CartItem.objects.select_for_update().get_or_create(
            cart=cart, product=product, defaults={"quantity": 0}
        )
        item.quantity = _validate_quantity(product, item.quantity + quantity)
        item.save(update_fields=["quantity", "updated_at"])
        return item


def set_cart_quantity(*, user, product: Product, quantity: int) -> CartItem | None:
    """Set an absolute quantity. ``0`` removes the line. Returns ``None`` if removed."""
    if quantity < 0:
        raise ValidationError("Quantity cannot be negative.")

    with transaction.atomic():
        cart = get_or_create_cart(user)
        if quantity == 0:
            CartItem.objects.filter(cart=cart, product=product).delete()
            return None
        _validate_quantity(product, quantity)
        item, _created = CartItem.objects.update_or_create(
            cart=cart, product=product, defaults={"quantity": quantity}
        )
        return item


def remove_from_cart(*, user, product: Product) -> None:
    cart = get_or_create_cart(user)
    CartItem.objects.filter(cart=cart, product=product).delete()


def clear_cart(*, user) -> None:
    cart = get_or_create_cart(user)
    cart.items.all().delete()


@transaction.atomic
def place_order(*, user, shipping: dict) -> Order:
    """Turn the cart into a pending order.

    Stock is checked once more under a row lock, prices are snapshotted, and
    the cart is emptied. The order is not paid yet: only the gateway callback
    may mark it paid.
    """
    cart = (
        Cart.objects.select_for_update()
        .prefetch_related("items__product")
        .filter(user=user)
        .first()
    )
    if cart is None or cart.is_empty:
        raise ValidationError("Your cart is empty.")

    items = list(cart.items.select_related("product"))
    for item in items:
        if not item.product.is_active:
            raise ValidationError(f"{item.product.name} is no longer available.")
        if item.quantity > item.product.stock:
            raise ValidationError(
                f"Only {item.product.stock} of {item.product.name} left in stock."
            )

    total = sum((item.line_total for item in items), Decimal("0.00"))

    order = Order.objects.create(
        user=user,
        total=total,
        ship_to_name=shipping["name"],
        ship_to_phone=shipping["phone"],
        ship_to_address=shipping["address"],
        ship_to_city=shipping["city"],
        ship_to_postcode=shipping.get("postcode", ""),
    )
    OrderItem.objects.bulk_create(
        OrderItem(
            order=order,
            product=item.product,
            product_name=item.product.name,
            product_image=item.product.image.name if item.product.image else "",
            unit_price=item.product.price,
            quantity=item.quantity,
        )
        for item in items
    )
    cart.items.all().delete()
    return order


@transaction.atomic
def mark_order_paid(*, order: Order, transaction_id: str, validation_id: str = "") -> Order:
    """Settle an order. Idempotent: replaying a gateway callback is a no-op."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status == Order.Status.PAID:
        logger.info("Ignoring duplicate payment callback for order %s", locked.reference)
        return locked

    # Stock leaves the shelf only once payment is confirmed.
    for item in locked.items.select_related("product"):
        if item.product is None:
            continue
        remaining = max(0, item.product.stock - item.quantity)
        Product.objects.filter(pk=item.product_id).update(stock=remaining)

    locked.status = Order.Status.PAID
    locked.transaction_id = transaction_id
    locked.validation_id = validation_id
    locked.paid_at = timezone.now()
    locked.save(update_fields=["status", "transaction_id", "validation_id", "paid_at", "updated_at"])
    return locked


@transaction.atomic
def mark_order_failed(*, order: Order, reason: str = "") -> Order:
    """Record a failed attempt.

    The status is re-read under a lock rather than trusted from the passed-in
    instance: a late failure callback must never undo a settled payment.
    """
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status == Order.Status.PAID:
        logger.info("Ignoring failure callback for already-paid order %s", locked.reference)
        return locked

    locked.status = Order.Status.FAILED
    locked.save(update_fields=["status", "updated_at"])
    logger.warning("Order %s failed: %s", locked.reference, reason or "no reason given")
    return locked


@transaction.atomic
def cancel_order(*, user, order: Order) -> Order:
    """Cancel an unpaid order. Ownership and status are both re-checked here."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.user_id != user.id:
        raise PermissionError("You can only cancel your own orders.")
    if locked.status != Order.Status.PENDING:
        raise ValidationError("Only orders awaiting payment can be cancelled.")

    locked.status = Order.Status.CANCELLED
    locked.save(update_fields=["status", "updated_at"])
    return locked
