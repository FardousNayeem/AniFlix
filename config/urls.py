"""Root URL configuration.

Each product area owns its own URLconf and namespace, so routes can move
without touching this file.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("profile/", include("apps.accounts.urls")),
    path("events/", include("apps.events.urls")),
    path("shop/", include("apps.shop.urls")),
    path("", include("apps.streaming.urls")),
]

handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += [
        path("__errors__/404/", core_views.preview_404),
        path("__errors__/500/", core_views.preview_500),
    ]
