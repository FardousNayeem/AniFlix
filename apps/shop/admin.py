from django.contrib import admin
from django.utils.html import format_html

from .models import Cart, CartItem, Order, OrderItem, Product


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    readonly_fields = ["created_at"]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ["product", "product_name", "unit_price", "quantity", "line_total"]
    can_delete = False

    @admin.display(description="Line total")
    def line_total(self, obj):
        return obj.line_total


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "collection", "price", "stock", "size", "is_active", "image_preview"]
    list_filter = ["is_active", "size", "collection", "related_anime"]
    list_editable = ["price", "stock", "is_active"]
    search_fields = ["name", "description", "collection"]
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ["related_anime"]
    readonly_fields = ["image_preview", "created_at", "updated_at"]

    @admin.display(description="Image")
    def image_preview(self, obj):
        if obj.image_url:
            return format_html('<img src="{}" style="height:60px;border-radius:6px" />', obj.image_url)
        return "No image"


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ["user", "items_count", "subtotal", "updated_at"]
    search_fields = ["user__email"]
    inlines = [CartItemInline]
    list_select_related = ["user"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["reference", "user", "status", "total", "created_at", "paid_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["reference", "user__email", "transaction_id"]
    list_select_related = ["user"]
    date_hierarchy = "created_at"
    inlines = [OrderItemInline]
    readonly_fields = [
        "reference",
        "user",
        "total",
        "transaction_id",
        "validation_id",
        "paid_at",
        "created_at",
        "updated_at",
        "ship_to_name",
        "ship_to_phone",
        "ship_to_address",
        "ship_to_city",
        "ship_to_postcode",
    ]

    def has_add_permission(self, request) -> bool:
        # Orders are created by checkout, never by hand.
        return False
