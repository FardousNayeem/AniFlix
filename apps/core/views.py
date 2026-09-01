"""Error pages. Kept in core so every app renders the same shell."""

from django.http import HttpResponse
from django.shortcuts import render
from django.template import RequestContext  # noqa: F401  (documented Django handler contract)


def page_not_found(request, exception=None):  # noqa: ARG001
    return render(request, "core/404.html", status=404)


def server_error(request):
    return render(request, "core/500.html", status=500)


def preview_404(request) -> HttpResponse:
    """DEBUG-only route so the 404 design can be reviewed without breaking a URL."""
    return page_not_found(request)


def preview_500(request) -> HttpResponse:
    """DEBUG-only route so the 500 design can be reviewed without raising."""
    return server_error(request)
