"""Store views.

Everything that touches money is login-gated, POST-only, and scoped to the
requesting user. Payment state is only ever changed from a verified gateway
callback, never from something the browser asserted.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, View

from apps.core.pagination import paginate, querystring_without_page

from . import selectors, services
from .forms import CartUpdateForm, OrderFilterForm, ShippingForm
from .models import Order, Product
from .payments import PaymentError, get_gateway

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
class ProductListView(TemplateView):
    """Browsable without an account.

    The old shop was behind ``@login_required``, so anonymous visitors were
    bounced to a login screen before they could see a single product.
    """

    template_name = "shop/product_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request
        search = request.GET.get("q", "").strip()
        sort = request.GET.get("sort", selectors.DEFAULT_SORT)
        if sort not in selectors.SORT_OPTIONS:
            sort = selectors.DEFAULT_SORT

        queryset = selectors.product_catalog(search=search, sort=sort)
        page = paginate(request, queryset, settings.SHOP_PAGE_SIZE)
        context.update(
            {
                "page_obj": page,
                "products": page.object_list,
                "search": search,
                "sort": sort,
                "sort_options": selectors.SORT_OPTIONS,
                "extra_query": querystring_without_page(request),
                "result_count": page.paginator.count,
            }
        )
        return context


class ProductDetailView(View):
    def get(self, request, slug: str):
        product = selectors.product_detail(slug=slug)
        return render(
            request,
            "shop/product_detail.html",
            {
                "product": product,
                "related": selectors.related_products(product),
                "max_quantity": min(settings.CART_MAX_QUANTITY_PER_ITEM, product.stock or 1),
            },
        )


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------
class CartView(LoginRequiredMixin, TemplateView):
    template_name = "shop/cart.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cart_obj"] = selectors.cart_with_items(self.request.user)
        context["max_quantity"] = settings.CART_MAX_QUANTITY_PER_ITEM
        return context


@login_required
@require_POST
def update_cart_view(request, slug: str):
    """Single entry point for add/remove/set/delete.

    Answers JSON to fetch() and redirects for no-JS clients, so the cart works
    with scripting disabled.
    """
    product = get_object_or_404(Product.objects.available(), slug=slug)
    form = CartUpdateForm(request.POST)

    if not form.is_valid():
        return _cart_response(request, ok=False, message="That cart update was not valid.", status=400)

    operation = form.cleaned_data["op"]
    quantity = form.cleaned_data.get("quantity") or 1

    try:
        if operation == "add":
            services.add_to_cart(user=request.user, product=product, quantity=quantity)
            message = f"{product.name} added to your cart."
        elif operation == "remove":
            item = selectors.get_cart(request.user)
            current = 0
            if item is not None:
                line = item.items.filter(product=product).first()
                current = line.quantity if line else 0
            services.set_cart_quantity(user=request.user, product=product, quantity=max(0, current - 1))
            message = "Cart updated."
        elif operation == "set":
            services.set_cart_quantity(user=request.user, product=product, quantity=quantity)
            message = "Cart updated."
        else:  # delete
            services.remove_from_cart(user=request.user, product=product)
            message = f"{product.name} removed from your cart."
    except ValidationError as exc:
        return _cart_response(request, ok=False, message=exc.messages[0], status=400)

    return _cart_response(request, ok=True, message=message)


def _cart_response(request, *, ok: bool, message: str, status: int = 200):
    summary = selectors.cart_summary(request.user)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        cart = selectors.cart_with_items(request.user)
        lines = (
            {
                str(item.product.slug): {
                    "quantity": item.quantity,
                    "line_total": f"{item.line_total:.2f}",
                }
                for item in cart.items.all()
            }
            if cart
            else {}
        )
        return JsonResponse(
            {
                "ok": ok,
                "message": message,
                "count": summary["count"],
                "subtotal": f"{summary['subtotal']:.2f}",
                "currency": settings.CURRENCY_SYMBOL,
                "lines": lines,
            },
            status=status,
        )

    if ok:
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect(request.META.get("HTTP_REFERER") or reverse("shop:cart"))


# ---------------------------------------------------------------------------
# Checkout and payment
# ---------------------------------------------------------------------------
class CheckoutView(LoginRequiredMixin, View):
    template_name = "shop/checkout.html"

    def get(self, request):
        cart = selectors.cart_with_items(request.user)
        if cart is None or cart.is_empty:
            messages.info(request, "Your cart is empty.")
            return redirect("shop:product-list")
        form = ShippingForm(initial=ShippingForm.initial_from_user(request.user))
        return render(request, self.template_name, {"cart_obj": cart, "form": form})

    def post(self, request):
        cart = selectors.cart_with_items(request.user)
        if cart is None or cart.is_empty:
            messages.info(request, "Your cart is empty.")
            return redirect("shop:product-list")

        form = ShippingForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Check the delivery details below.")
            return render(request, self.template_name, {"cart_obj": cart, "form": form}, status=400)

        if form.cleaned_data["save_to_profile"]:
            _save_shipping_to_profile(request.user, form.cleaned_data)

        try:
            order = services.place_order(user=request.user, shipping=form.cleaned_data)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect("shop:cart")

        return redirect("shop:payment-start", reference=order.reference)


def _save_shipping_to_profile(user, data: dict) -> None:
    user.phone = data["phone"]
    user.address = data["address"]
    user.city = data["city"]
    user.postcode = data.get("postcode", "")
    if not user.display_name:
        user.display_name = data["name"]
    user.save(update_fields=["phone", "address", "city", "postcode", "display_name"])


@login_required
def start_payment_view(request, reference: str):
    """Open a gateway session for an order this user owns and has not paid."""
    order = selectors.order_for_user(user=request.user, reference=reference)
    if order.is_paid:
        return redirect(order)
    if order.status == Order.Status.CANCELLED:
        messages.error(request, "That order was cancelled.")
        return redirect("shop:order-list")

    gateway = get_gateway()
    base = request.build_absolute_uri("/").rstrip("/")
    try:
        session = gateway.create_session(
            order=order,
            success_url=f"{base}{reverse('shop:payment-return', args=[order.reference])}",
            fail_url=f"{base}{reverse('shop:payment-return', args=[order.reference])}?status=FAILED",
            cancel_url=f"{base}{reverse('shop:payment-return', args=[order.reference])}?status=CANCELLED",
            ipn_url=f"{base}{reverse('shop:payment-ipn', args=[order.reference])}",
        )
    except PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("shop:checkout")

    return redirect(session.redirect_url)


@csrf_exempt
def payment_return_view(request, reference: str):
    """Where the gateway sends the customer back.

    CSRF-exempt because SSLCommerz POSTs here from its own domain. That is
    exactly why the payload is never trusted: the result is confirmed against
    the gateway before anything is marked paid.
    """
    order = get_object_or_404(Order, reference=reference)
    payload = request.POST.dict() if request.method == "POST" else request.GET.dict()

    result = _settle(order, payload)
    if result is None:
        messages.error(request, "We could not confirm that payment. Nothing was charged twice.")
        return redirect("shop:order-detail", reference=order.reference)

    if result.is_successful:
        messages.success(request, "Payment received. Your order is confirmed.")
    else:
        messages.error(request, result.reason or "The payment did not go through.")
    return redirect("shop:order-detail", reference=order.reference)


@csrf_exempt
@require_POST
def payment_ipn_view(request, reference: str) -> HttpResponse:
    """Server-to-server notification.

    The customer's browser can die on the way back from the gateway; this is
    the callback that guarantees a paid order is recorded as paid.
    """
    order = get_object_or_404(Order, reference=reference)
    result = _settle(order, request.POST.dict())
    if result is None:
        return HttpResponse("verification failed", status=502)
    return HttpResponse("ok" if result.is_successful else "rejected", status=200)


def _settle(order: Order, payload: dict):
    """Verify a callback with the gateway and apply the outcome. Idempotent."""
    gateway = get_gateway()
    try:
        result = gateway.verify(order=order, payload=payload)
    except PaymentError:
        logger.exception("Gateway verification errored for order %s", order.reference)
        return None

    if result.is_successful:
        services.mark_order_paid(
            order=order,
            transaction_id=result.transaction_id,
            validation_id=result.validation_id,
        )
    else:
        services.mark_order_failed(order=order, reason=result.reason)
    return result


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
class OrderListView(LoginRequiredMixin, TemplateView):
    template_name = "shop/order_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = OrderFilterForm(self.request.GET or None)
        orders = selectors.orders_for_user(self.request.user)
        if form.is_valid() and form.cleaned_data.get("status"):
            orders = orders.filter(status=form.cleaned_data["status"])
        context["orders"] = orders
        context["filter_form"] = form
        return context


class OrderDetailView(LoginRequiredMixin, View):
    """The receipt. Ownership is enforced by the selector's queryset filter."""

    def get(self, request, reference: str):
        order = selectors.order_for_user(user=request.user, reference=reference)
        return render(request, "shop/order_detail.html", {"order": order})


@login_required
@require_POST
def cancel_order_view(request, reference: str):
    order = selectors.order_for_user(user=request.user, reference=reference)
    try:
        services.cancel_order(user=request.user, order=order)
    except (ValidationError, PermissionError) as exc:
        message = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
        messages.error(request, message)
    else:
        messages.success(request, f"Order {order.reference} cancelled.")
    return redirect("shop:order-list")
