"""Values every template needs, resolved once per request."""

from django.conf import settings


def site(request):
    return {
        "SITE_NAME": settings.SITE_NAME,
        "CURRENCY_SYMBOL": settings.CURRENCY_SYMBOL,
        "CURRENCY_CODE": settings.CURRENCY_CODE,
    }
