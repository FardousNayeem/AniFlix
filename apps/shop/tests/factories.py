from decimal import Decimal

from apps.accounts.models import User
from apps.shop.models import Product


def make_user(email="shopper@example.com", **kwargs) -> User:
    defaults = {
        "display_name": "Rumi Ahsan",
        "phone": "+8801711223344",
        "address": "12 Green Road",
        "city": "Dhaka",
        "postcode": "1205",
    }
    defaults.update(kwargs)
    return User.objects.create_user(email=email, password="test-pass-123", **defaults)


def make_product(name="Berserk Black T-Shirt", price="999.00", stock=10, **kwargs) -> Product:
    defaults = {"description": "Heavyweight cotton.", "is_active": True}
    defaults.update(kwargs)
    return Product.objects.create(name=name, price=Decimal(price), stock=stock, **defaults)


SHIPPING = {
    "name": "Rumi Ahsan",
    "phone": "+8801711223344",
    "address": "12 Green Road",
    "city": "Dhaka",
    "postcode": "1205",
}
