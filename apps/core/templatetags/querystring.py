"""Querystring manipulation for filter and sort controls."""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs) -> str:
    """Return the current querystring with ``kwargs`` replaced.

    Used by sort/filter controls so selecting a sort does not drop the active
    search term (a real bug in the previous shop page).
    """
    request = context["request"]
    params = request.GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    params.pop("page", None)
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"
