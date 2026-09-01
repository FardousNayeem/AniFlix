"""allauth adapters.

The previous build had two competing sign-up paths (a hand-rolled view plus
allauth) which is why the custom login template went missing without anyone
noticing. There is now exactly one path, and these adapters are where product
rules attach to it.
"""

from __future__ import annotations

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    def save_user(self, request, user, form, commit: bool = True):
        user = super().save_user(request, user, form, commit=False)
        user.username = user.email
        if not user.display_name:
            user.display_name = user.email.split("@", 1)[0]
        if commit:
            user.save()
        return user


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def populate_user(self, request, sociallogin, data):
        """Seed the profile from the Google payload so new accounts are not blank."""
        user = super().populate_user(request, sociallogin, data)
        full_name = (data.get("name") or "").strip()
        first_last = " ".join(filter(None, [data.get("first_name"), data.get("last_name")])).strip()
        user.display_name = full_name or first_last or (user.email or "").split("@", 1)[0]
        user.username = user.email or user.username
        return user
