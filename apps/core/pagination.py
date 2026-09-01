"""Pagination helper shared by every list page.

Django's ``Paginator`` already does the work; this only removes the
copy-pasted try/except blocks and keeps querystrings intact across pages.
"""

from __future__ import annotations

from django.core.paginator import EmptyPage, Page, PageNotAnInteger, Paginator
from django.db.models import QuerySet
from django.http import HttpRequest


def paginate(request: HttpRequest, queryset: QuerySet, per_page: int) -> Page:
    """Return the requested page, clamping out-of-range values instead of 404ing."""
    paginator = Paginator(queryset, per_page)
    raw_page = request.GET.get("page", 1)
    try:
        return paginator.page(raw_page)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


def querystring_without_page(request: HttpRequest) -> str:
    """Current querystring minus ``page``, ready to append to a page link."""
    params = request.GET.copy()
    params.pop("page", None)
    encoded = params.urlencode()
    return f"&{encoded}" if encoded else ""
