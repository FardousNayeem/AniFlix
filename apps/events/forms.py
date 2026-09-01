from django import forms


class RegistrationForm(forms.Form):
    """Confirms the contact details attached to a seat."""

    contact_email = forms.EmailField(
        label="Contact email", widget=forms.EmailInput(attrs={"autocomplete": "email"})
    )
    contact_phone = forms.CharField(
        label="Contact phone",
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel"}),
        help_text="Optional. Used only if the organiser needs to reach you.",
    )

    def clean_contact_phone(self) -> str:
        phone = (self.cleaned_data.get("contact_phone") or "").strip()
        if phone and not phone.replace("+", "").replace("-", "").replace(" ", "").isdigit():
            raise forms.ValidationError("Enter a phone number using digits, spaces, '+' or '-'.")
        return phone
