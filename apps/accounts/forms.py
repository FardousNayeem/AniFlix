"""Forms for the profile area."""

from django import forms

from .models import Gender, User


class ProfileForm(forms.ModelForm):
    """Edits profile data only. Email and password are owned by allauth."""

    class Meta:
        model = User
        fields = [
            "avatar",
            "display_name",
            "bio",
            "gender",
            "phone",
            "address",
            "city",
            "postcode",
            "newsletter_opt_in",
        ]
        widgets = {
            "display_name": forms.TextInput(attrs={"placeholder": "How other fans see you"}),
            "bio": forms.Textarea(attrs={"rows": 4, "placeholder": "Favourite arcs, hot takes, anything."}),
            "phone": forms.TextInput(attrs={"placeholder": "+8801XXXXXXXXX", "inputmode": "tel"}),
            "address": forms.TextInput(attrs={"placeholder": "House, road, area"}),
            "city": forms.TextInput(attrs={"placeholder": "Dhaka"}),
            "postcode": forms.TextInput(attrs={"placeholder": "1212", "inputmode": "numeric"}),
            "gender": forms.Select(choices=Gender.choices),
        }
        labels = {
            "newsletter_opt_in": "Email me new episodes and drops",
            "display_name": "Display name",
            "postcode": "Postcode",
        }
        help_texts = {
            "avatar": "Square images look best. PNG or JPG.",
            "bio": "Shown on your profile. 500 characters max.",
        }

    def clean_display_name(self) -> str:
        return (self.cleaned_data.get("display_name") or "").strip()

    def clean_phone(self) -> str:
        phone = (self.cleaned_data.get("phone") or "").strip()
        if phone and not phone.replace("+", "").replace("-", "").replace(" ", "").isdigit():
            raise forms.ValidationError("Enter a phone number using digits, spaces, '+' or '-'.")
        return phone
