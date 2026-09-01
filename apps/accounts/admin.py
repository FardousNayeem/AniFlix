from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["-date_joined"]
    list_display = ["email", "display_name", "is_staff", "newsletter_opt_in", "date_joined"]
    list_filter = ["is_staff", "is_superuser", "is_active", "newsletter_opt_in"]
    search_fields = ["email", "display_name", "username"]

    fieldsets = (
        (None, {"fields": ("email", "username", "password")}),
        (_("Profile"), {"fields": ("display_name", "bio", "avatar", "gender")}),
        (_("Contact"), {"fields": ("phone", "address", "city", "postcode")}),
        (_("Preferences"), {"fields": ("newsletter_opt_in",)}),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "display_name", "password1", "password2")}),
    )
