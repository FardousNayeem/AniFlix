"""Presentation-only template helpers.

Nothing here may touch the database: these exist so templates stop carrying
formatting logic inline.
"""

from __future__ import annotations

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def money(value) -> str:
    """Format a decimal as the store currency, e.g. ``৳1,250.00``."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = 0.0
    return f"{settings.CURRENCY_SYMBOL}{amount:,.2f}"


@register.filter
def initials(value) -> str:
    """Two-letter fallback avatar text for a name or email."""
    text = (value or "").strip()
    if not text:
        return "?"
    if "@" in text:
        text = text.split("@", 1)[0]
    parts = [part for part in text.replace(".", " ").replace("_", " ").split() if part]
    if not parts:
        return text[0].upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


@register.simple_tag
def stars(score, out_of: int = 5) -> str:
    """Render a static star meter. ``score`` may be ``None`` for 'not rated'."""
    try:
        value = float(score or 0)
    except (TypeError, ValueError):
        value = 0.0
    filled = int(round(value))
    filled = max(0, min(out_of, filled))
    glyphs = "".join(
        f'<span class="star{" is-filled" if index < filled else ""}" aria-hidden="true"></span>'
        for index in range(out_of)
    )
    label = f"{value:.1f} out of {out_of}" if value else "Not rated yet"
    return mark_safe(f'<span class="stars" role="img" aria-label="{label}">{glyphs}</span>')
