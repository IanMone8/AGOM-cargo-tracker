from django import template

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
    return "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold " + BADGES.get(
        value, "bg-slate-100 text-slate-700"
    )
