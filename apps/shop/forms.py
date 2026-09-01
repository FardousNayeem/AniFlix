"""Store forms."""

from django import forms

from .models import Order


class ShippingForm(forms.Form):
    """Collected at checkout and copied onto the order."""

    name = forms.CharField(label="Full name", max_length=200)
    phone = forms.CharField(label="Phone", max_length=32, widget=forms.TextInput(attrs={"inputmode": "tel"}))
    address = forms.CharField(label="Address", max_length=255)
    city = forms.CharField(label="City", max_length=120)
    postcode = forms.CharField(
        label="Postcode", max_length=20, required=False, widget=forms.TextInput(attrs={"inputmode": "numeric"})
    )
    save_to_profile = forms.BooleanField(
        label="Save these details to my profile", required=False, initial=True
    )

    @classmethod
    def initial_from_user(cls, user) -> dict:
        return {
            "name": user.public_name,
            "phone": user.phone,
            "address": user.address,
            "city": user.city,
            "postcode": user.postcode,
        }

    def clean_phone(self) -> str:
        phone = (self.cleaned_data.get("phone") or "").strip()
        digits = phone.replace("+", "").replace("-", "").replace(" ", "")
        if not digits.isdigit() or len(digits) < 6:
            raise forms.ValidationError("Enter a reachable phone number.")
        return phone


class CartUpdateForm(forms.Form):
    """Validates cart mutations coming from the page or from fetch().

    The field is called ``op`` and not ``action``: a control named ``action``
    becomes a property of the form element and shadows ``HTMLFormElement.action``,
    so ``form.action`` in JavaScript returns the input rather than the URL. That
    silently posted every cart update to ``/shop/[object HTMLInputElement]``.
    """

    OPERATIONS = ["add", "remove", "set", "delete"]

    op = forms.ChoiceField(choices=[(value, value) for value in OPERATIONS])
    quantity = forms.IntegerField(required=False, min_value=0, max_value=99)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("op") == "set" and cleaned.get("quantity") is None:
            raise forms.ValidationError("A quantity is required when setting an amount.")
        return cleaned


class OrderFilterForm(forms.Form):
    status = forms.ChoiceField(
        required=False,
        choices=[("", "All orders"), *Order.Status.choices],
        widget=forms.Select(attrs={"data-autosubmit": "true"}),
    )
