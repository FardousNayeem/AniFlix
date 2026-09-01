"""Store models.

The previous schema used one ``Order`` row for both "the cart" and "the
purchase", distinguished by a ``completed`` flag. That meant a placed order
recalculated its own total from live product prices, so editing a product in
the admin silently rewrote historical receipts. Cart and Order are separate
here, and an Order snapshots what was bought at the price that was paid.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


def product_image_upload_to(instance: "Product", filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"products/{uuid.uuid4().hex}.{suffix}"


class Size(models.TextChoices):
    NOT_APPLICABLE = "", "One size"
    SMALL = "S", "S"
    MEDIUM = "M", "M"
    LARGE = "L", "L"
    XL = "XL", "XL"
    XXL = "XXL", "XXL"


class ProductQuerySet(models.QuerySet):
    def available(self) -> "ProductQuerySet":
        return self.filter(is_active=True)

    def search(self, term: str) -> "ProductQuerySet":
        term = (term or "").strip()
        if not term:
            return self
        return self.filter(
            models.Q(name__icontains=term)
            | models.Q(description__icontains=term)
            | models.Q(collection__icontains=term)
            | models.Q(related_anime__name__icontains=term)
        ).distinct()


class Product(TimeStampedModel):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField(max_length=1000, blank=True)
    image = models.ImageField(upload_to=product_image_upload_to, blank=True, null=True)
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))]
    )
    size = models.CharField(max_length=8, choices=Size.choices, blank=True)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    collection = models.CharField(
        max_length=120,
        blank=True,
        help_text="Franchise this item belongs to, e.g. 'Cowboy Bebop'. Free text, so "
        "merch can exist for series the catalogue does not stream.",
    )
    related_anime = models.ForeignKey(
        "streaming.Anime",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merchandise",
        help_text="Set only when the franchise is a title we actually stream; "
        "it cross-sells the item on that anime's page.",
    )

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "name"]
        indexes = [models.Index(fields=["price"]), models.Index(fields=["-created_at"])]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:200] or "product"
            candidate = base
            suffix = 2
            while Product.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("shop:product-detail", kwargs={"slug": self.slug})

    @property
    def image_url(self) -> str | None:
        if self.image and hasattr(self.image, "url"):
            try:
                return self.image.url
            except ValueError:  # pragma: no cover
                return None
        return None

    @property
    def in_stock(self) -> bool:
        return self.is_active and self.stock > 0


class Cart(TimeStampedModel):
    """Exactly one open cart per user, enforced by the database."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart"
    )

    def __str__(self) -> str:
        return f"Cart for {self.user}"

    @property
    def items_count(self) -> int:
        return sum(item.quantity for item in self.items.all())

    @property
    def subtotal(self) -> Decimal:
        return sum((item.line_total for item in self.items.all()), ZERO)

    @property
    def is_empty(self) -> bool:
        return not self.items.exists()


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="cart_items")
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["cart", "product"], name="unique_product_per_cart")
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.product.name}"

    @property
    def line_total(self) -> Decimal:
        return self.product.price * self.quantity


class Order(TimeStampedModel):
    """An immutable record of a purchase."""

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting payment"
        PAID = "paid", "Paid"
        FAILED = "failed", "Payment failed"
        CANCELLED = "cancelled", "Cancelled"

    reference = models.CharField(max_length=20, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)

    # Shipping details, copied at checkout so later profile edits do not rewrite history.
    ship_to_name = models.CharField(max_length=200)
    ship_to_phone = models.CharField(max_length=32)
    ship_to_address = models.CharField(max_length=255)
    ship_to_city = models.CharField(max_length=120)
    ship_to_postcode = models.CharField(max_length=20, blank=True)

    # Gateway bookkeeping.
    transaction_id = models.CharField(max_length=120, blank=True, db_index=True)
    validation_id = models.CharField(max_length=120, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["-created_at"]), models.Index(fields=["status"])]

    def __str__(self) -> str:
        return self.reference

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f"OF-{uuid.uuid4().hex[:10].upper()}"
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("shop:order-detail", kwargs={"reference": self.reference})

    @property
    def items_count(self) -> int:
        return sum(item.quantity for item in self.items.all())

    @property
    def is_paid(self) -> bool:
        return self.status == self.Status.PAID


class OrderItem(models.Model):
    """A price and name snapshot. ``product`` may go away; the receipt may not."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    product_name = models.CharField(max_length=200)
    product_image = models.CharField(max_length=255, blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.product_name}"

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity
