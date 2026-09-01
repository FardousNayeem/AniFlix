"""The account model and the profile data that hangs off it."""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


def avatar_upload_to(instance: "User", filename: str) -> str:
    """Namespaced, collision-free upload path."""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    return f"avatars/{uuid.uuid4().hex}.{suffix}"


class UserManager(BaseUserManager):
    """Email-first manager. The old model kept ``username`` as the natural key
    while authenticating on email, which allowed two accounts for one person."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email).lower()
        extra_fields.setdefault("username", email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class Gender(models.TextChoices):
    UNSPECIFIED = "", _("Prefer not to say")
    FEMALE = "female", _("Female")
    MALE = "male", _("Male")
    NON_BINARY = "non_binary", _("Non-binary")


class User(AbstractUser):
    """Authentication identity plus the handful of profile fields the UI needs."""

    email = models.EmailField(_("email address"), unique=True)
    display_name = models.CharField(_("display name"), max_length=120, blank=True)
    bio = models.TextField(_("bio"), max_length=500, blank=True)
    avatar = models.ImageField(_("avatar"), upload_to=avatar_upload_to, blank=True, null=True)
    gender = models.CharField(_("gender"), max_length=20, choices=Gender.choices, blank=True)
    phone = models.CharField(_("phone"), max_length=32, blank=True)
    address = models.CharField(_("address"), max_length=255, blank=True)
    city = models.CharField(_("city"), max_length=120, blank=True)
    postcode = models.CharField(_("postcode"), max_length=20, blank=True)
    newsletter_opt_in = models.BooleanField(_("newsletter"), default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["-date_joined"]

    def __str__(self) -> str:
        return self.display_name or self.email

    def save(self, *args, **kwargs):
        # ``username`` stays on the model because AbstractUser and the admin
        # rely on it, but it is never the identity the product uses.
        self.email = self.email.lower()
        if not self.username:
            self.username = self.email
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("accounts:profile")

    @property
    def public_name(self) -> str:
        return self.display_name or self.email.split("@", 1)[0]

    @property
    def avatar_url(self) -> str | None:
        """``None`` means the template should render initials instead."""
        if self.avatar and hasattr(self.avatar, "url"):
            try:
                return self.avatar.url
            except ValueError:  # pragma: no cover - storage misconfiguration
                return None
        return None

    @property
    def has_shipping_details(self) -> bool:
        return bool(self.address and self.city and self.phone)
