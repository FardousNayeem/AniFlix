"""Read-side queries for the store."""

from __future__ import annotations

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from .models import Cart, Order, Product

SORT_OPTIONS = {
    "newest": ("-created_at", "New arrivals"),
    "oldest": ("created_at", "Oldest first"),
    "price_asc": ("price", "Price: low to high"),
    "price_desc": ("-price", "Price: high to low"),
    "name": ("name", "A to Z"),
}
DEFAULT_SORT = "newest"


def product_catalog(*, search: str = "", sort: str = DEFAULT_SORT) -> QuerySet[Product]:
    queryset = Product.objects.available().select_related("related_anime").search(search)
    order_field = SORT_OPTIONS.get(sort, SORT_OPTIONS[DEFAULT_SORT])[0]
    return queryset.order_by(order_field, "name")


def product_detail(*, slug: str) -> Product:
    return get_object_or_404(
        Product.objects.available().select_related("related_anime"), slug=slug
    )


def related_products(product: Product, *, limit: int = 4) -> QuerySet[Product]:
    queryset = Product.objects.available().exclude(pk=product.pk)
    if product.related_anime_id:
        matches = queryset.filter(related_anime_id=product.related_anime_id)
        if matches.exists():
            return matches[:limit]
    if product.collection:
        matches = queryset.filter(collection__iexact=product.collection)
        if matches.exists():
            return matches[:limit]
    return queryset.order_by("-created_at")[:limit]


def get_cart(user) -> Cart | None:
    """The user's cart if it exists. Never creates one on a read path."""
    if not user.is_authenticated:
        return None
    return Cart.objects.filter(user=user).first()


def cart_with_items(user) -> Cart | None:
    if not user.is_authenticated:
        return None
    return (
        Cart.objects.filter(user=user)
        .prefetch_related("items__product")
        .first()
    )


def cart_summary(user) -> dict:
    """Small payload the navbar badge needs on every page."""
    cart = cart_with_items(user)
    if cart is None:
        return {"count": 0, "subtotal": 0}
    return {"count": cart.items_count, "subtotal": cart.subtotal}


def orders_for_user(user) -> QuerySet[Order]:
    if not user.is_authenticated:
        return Order.objects.none()
    return Order.objects.filter(user=user).prefetch_related("items")


def recent_orders_for_user(user) -> QuerySet[Order]:
    return orders_for_user(user).filter(status=Order.Status.PAID)


def order_for_user(*, user, reference: str) -> Order:
    """Scoped to the requesting user.

    The old receipt view looked orders up by primary key with no ownership
    check, so any signed-in user could read anybody's address and order history
    by walking ``/shop/recipt/1``, ``/2``, ``/3``.
    """
    return get_object_or_404(
        Order.objects.prefetch_related("items").filter(user=user), reference=reference
    )
