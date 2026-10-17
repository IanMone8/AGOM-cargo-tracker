import os

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

BADGES = {
    # box status
    "packed": "pill-neutral",
    "shipped": "pill-info",
    "arrived": "pill-warn",
    "checked": "pill-ok",
    # check result
    "matched": "pill-ok",
    "missing": "pill-danger",
    "wrong_qty": "pill-warn",
    # order (customer request) status
    "open": "pill-warn",
    "ordered": "pill-info",
    "in_box": "pill-info",
    "fulfilled": "pill-ok",
}


@register.filter
def ks(value):
    """450000 -> "450,000 Ks" (whole Myanmar Kyat); empty for no amount. Negative: "−50,000 Ks"."""
    if value in (None, ""):
        return ""
    value = int(value)
    return f"{'−' if value < 0 else ''}{abs(value):,} Ks"


@register.filter
def yen(value):
    """12500 -> "¥12,500" (whole yen); empty for no amount."""
    if value in (None, ""):
        return ""
    return f"¥{int(value):,}"


@register.filter
def badge(value):
    """Status pill; colours come from the tokens in css/agom.css (all pass WCAG AA)."""
    return "pill " + BADGES.get(value, "pill-neutral")


# Outline icons (24x24, stroke) used in the sidebar, cards and buttons.
ICONS = {
    "home": "M3 10.5 12 3l9 7.5M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5",
    "box": "M21 7.5 12 3 3 7.5m18 0-9 4.5m9-4.5v9L12 21m0-9L3 7.5M12 12v9M3 7.5v9L12 21",
    "history": "M12 7v5l3 2m6-2a9 9 0 1 1-2.64-6.36M21 4v4h-4",
    "users": "M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1m13-8a3 3 0 1 0 0-6m5 14v-1a4 4 0 0 0-3-3.87M9.5 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
    "contact": "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Zm5 9a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Zm-4 3c.6-1.7 2.2-2.5 4-2.5s3.4.8 4 2.5M15 10h3m-3 3h3",
    "phone": "M5 4h3l2 5-2.5 1.5a11 11 0 0 0 6 6L15 14l5 2v3a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2",
    "pin": "M12 21s-7-6.2-7-11.5a7 7 0 1 1 14 0C19 14.8 12 21 12 21Zm0-9a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
    "search": "M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15Zm5.3-2.2L21 21",
    "chat": "M4 5h16v11H8l-4 4V5Z",
    "dots": "M6.5 12a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Zm7 0a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Zm7 0a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Z",
    "copy": "M9 9h10v11H9zM5 15V4h10",
    "upload": "M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2M12 15V4m0 0L7.5 8.5M12 4l4.5 4.5",
    "cog": "M10.3 4.3a1.7 1.7 0 0 1 3.4 0 1.7 1.7 0 0 0 2.6 1 1.7 1.7 0 0 1 2.4 2.4 1.7 1.7 0 0 0 1 2.6 1.7 1.7 0 0 1 0 3.4 1.7 1.7 0 0 0-1 2.6 1.7 1.7 0 0 1-2.4 2.4 1.7 1.7 0 0 0-2.6 1 1.7 1.7 0 0 1-3.4 0 1.7 1.7 0 0 0-2.6-1 1.7 1.7 0 0 1-2.4-2.4 1.7 1.7 0 0 0-1-2.6 1.7 1.7 0 0 1 0-3.4 1.7 1.7 0 0 0 1-2.6 1.7 1.7 0 0 1 2.4-2.4 1.7 1.7 0 0 0 2.6-1ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
    "logout": "M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5m5 5H3",
    "plus": "M12 5v14m-7-7h14",
    "x": "M6 6l12 12M18 6 6 18",
    "trash": "M4 7h16m-10 4v6m4-6v6M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-12M9 7V4h6v3",
    "check": "M9 12.5l2 2 4-4.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
    "alert": "M12 9v4m0 4h.01M10.3 3.9 2.4 17.5A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
    "ship": "M3 17l1.5 3h15L21 17M5 17V11l7-3 7 3v6M12 8V3m0 0H8m4 0h4",
    "flag": "M5 21V4m0 0h11l-2 4 2 4H5",
    "menu": "M4 6h16M4 12h16M4 18h16",
    "inbox": "M3 13h5l1.5 3h5L16 13h5M5 5h14l2 8v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5l2-8Z",
    "play": "M8 5.5v13l11-6.5-11-6.5Z",
    # golf bag: body with a pocket, strap and three club heads sticking out
    "bag": "M8 8h8l-.6 12.2a1 1 0 0 1-1 .8H9.6a1 1 0 0 1-1-.8L8 8Zm1.5 0V4.5L8 3m3.5 5V3.5h1.5M14.5 8V5l1.5-1M9 13h6M16 9.5c2 .5 2.5 6 .5 8",
    "edit": "M4 20h4L19 9l-4-4L4 16v4Zm9-13 4 4",
    "chevron-down": "m6 9 6 6 6-6",
}


@register.simple_tag
def icon(name, cls="w-5 h-5"):
    from django.utils.html import format_html

    return format_html(
        '<svg class="{}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{}"/></svg>',
        cls, ICONS.get(name, ""),
    )


@register.simple_tag
def static_v(path):
    """Like {% static %}, but in development adds ?v=<file mtime> so browsers
    pick up edited CSS/images straight away (production URLs are already hashed)."""
    url = static(path)
    if settings.DEBUG:
        found = finders.find(path)
        if found:
            url += f"?v={int(os.path.getmtime(found))}"
    return url


# Customer avatars: the palette's tints with their AA text colour.
AVATAR_COLORS = [
    ("var(--mint)", "var(--ok-ink)"), ("var(--sage)", "var(--text)"), ("var(--cream)", "var(--warn-ink)"),
    ("var(--sand)", "var(--warn-ink)"), ("var(--info-bg)", "var(--info-ink)"), ("var(--grad-to)", "var(--text)"),
]


@register.filter
def initials(name):
    parts = (name or "").split()
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


@register.filter
def avatar_style(name):
    """Stable pastel colour per name."""
    import zlib

    bg, fg = AVATAR_COLORS[zlib.crc32((name or "").lower().encode()) % len(AVATAR_COLORS)]
    return f"background:{bg};color:{fg}"


@register.filter
def ago(value):
    """'5 days ago' – one unit only; 'just now' under a minute."""
    if not value:
        return ""
    from django.utils import timezone
    from django.utils.timesince import timesince

    if (timezone.now() - value).total_seconds() < 60:
        return "just now"
    return f"{timesince(value, depth=1)} ago"


@register.filter
def get_item(mapping, key):
    return mapping.get(key, "")
