import os

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

BADGES = {
    # box status
    "packed": "bg-slate-100 text-slate-700",
    "shipped": "bg-sky-100 text-sky-800",
    "arrived": "bg-amber-100 text-amber-800",
    "checked": "bg-emerald-100 text-emerald-800",
    # check result
    "matched": "bg-emerald-100 text-emerald-800",
    "missing": "bg-red-100 text-red-800",
    "wrong_qty": "bg-orange-100 text-orange-800",
    # request status
    "open": "bg-rose-100 text-rose-800",
    "ordered": "bg-sky-100 text-sky-800",
    "in_box": "bg-violet-100 text-violet-800",
    "fulfilled": "bg-emerald-100 text-emerald-800",
}


@register.filter
def badge(value):
    return "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold " + BADGES.get(
        value, "bg-slate-100 text-slate-700"
    )


# Outline icons (24x24, stroke) used in the sidebar, cards and buttons.
ICONS = {
    "home": "M3 10.5 12 3l9 7.5M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5",
    "box": "M21 7.5 12 3 3 7.5m18 0-9 4.5m9-4.5v9L12 21m0-9L3 7.5M12 12v9M3 7.5v9L12 21",
    "history": "M12 7v5l3 2m6-2a9 9 0 1 1-2.64-6.36M21 4v4h-4",
    "users": "M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1m13-8a3 3 0 1 0 0-6m5 14v-1a4 4 0 0 0-3-3.87M9.5 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
    "contact": "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Zm5 9a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Zm-4 3c.6-1.7 2.2-2.5 4-2.5s3.4.8 4 2.5M15 10h3m-3 3h3",
    "phone": "M5 4h3l2 5-2.5 1.5a11 11 0 0 0 6 6L15 14l5 2v3a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2",
    "pin": "M12 21s-7-6.2-7-11.5a7 7 0 1 1 14 0C19 14.8 12 21 12 21Zm0-9a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
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
