"""The four AGOM users map onto these roles.

- Admin            -> Django superuser (maintains the site, uses /admin)
- Owner            -> group "Owner": can see and do everything in the app
- Japan sender     -> group "Japan Sender": boxes, Japan lists, CSV import
- Myanmar checker  -> group "Myanmar Checker": arrival, receive checks, extra items

Everyone logged in can view every page and manage customer requests.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied

OWNER = "Owner"
JAPAN = "Japan Sender"
MYANMAR = "Myanmar Checker"
ALL_GROUPS = [OWNER, JAPAN, MYANMAR]


def _in_group(user, name):
    if not user.is_authenticated:
        return False
    cache = getattr(user, "_agom_groups", None)
    if cache is None:
        cache = set(user.groups.values_list("name", flat=True))
        user._agom_groups = cache
    return name in cache


def is_manager(user):
    return user.is_authenticated and (user.is_superuser or _in_group(user, OWNER))


def can_edit_japan(user):
    """Create boxes, edit Japan lists, import CSV."""
    return is_manager(user) or _in_group(user, JAPAN)


def can_check(user):
    """Record Myanmar arrival, receive checks and extra items."""
    return is_manager(user) or _in_group(user, MYANMAR)


def can_edit_box_items(user, box):
    """Edit the Japan side of a box (box header, add/delete items, CSV import).

    The Japan sender loses this once the Myanmar check has started;
    Owner and Admin can still correct things.
    """
    return is_manager(user) or (can_edit_japan(user) and not box.myanmar_started)


def can_edit_box(user, box):
    """Open the box Edit page (Myanmar checker edits the Myanmar side)."""
    return can_edit_box_items(user, box) or can_check(user)


def role_name(user):
    if user.is_superuser:
        return "Admin"
    for name in ALL_GROUPS:
        if _in_group(user, name):
            return name
    return "Viewer"


def require(check):
    """View decorator: 403 unless check(user) is true."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not check(request.user):
                raise PermissionDenied("Your role cannot do this.")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
