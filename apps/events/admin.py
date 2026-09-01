from django.contrib import admin
from django.utils.html import format_html

from .models import Event, Registration, Venue


class RegistrationInline(admin.TabularInline):
    model = Registration
    extra = 0
    readonly_fields = ["user", "contact_email", "contact_phone", "reference", "created_at"]
    can_delete = False


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ["name", "city", "address"]
    search_fields = ["name", "city", "address"]


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["title", "starts_at", "venue", "seats_display", "is_published"]
    list_filter = ["is_published", "starts_at", "venue"]
    list_editable = ["is_published"]
    search_fields = ["title", "description", "host_name"]
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "starts_at"
    inlines = [RegistrationInline]
    list_select_related = ["venue"]
    fieldsets = (
        (None, {"fields": ("title", "slug", "summary", "description", "cover")}),
        ("When and where", {"fields": ("starts_at", "ends_at", "venue")}),
        ("Organiser", {"fields": ("host_name", "organiser_email")}),
        ("Capacity", {"fields": ("capacity", "is_published")}),
    )

    @admin.display(description="Seats")
    def seats_display(self, obj):
        if obj.capacity == 0:
            return format_html("{} registered (unlimited)", obj.seats_taken)
        return format_html("{} / {}", obj.seats_taken, obj.capacity)


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ["reference", "user", "event", "contact_email", "created_at"]
    search_fields = ["reference", "user__email", "event__title", "contact_email"]
    list_select_related = ["user", "event"]
    readonly_fields = ["reference"]
