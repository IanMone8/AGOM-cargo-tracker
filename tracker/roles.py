"""The four AGOM users map onto these roles.

- Admin            -> Django superuser (maintains the site, uses /admin)
- Owner            -> group "Owner": can see and do everything in the app
- Japan sender     -> group "Japan Sender": boxes, Japan lists, CSV import
- Myanmar checker  -> group "Myanmar Checker": arrival, receive checks, extra items

CSV import is Owner/Admin only; customer requests are hidden from the Japan sender.
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


MYANMAR_STATUSES = ("arrived", "checked")       # box statuses only the Myanmar side sets


def can_set_box_status(user, status):
    """Arrived / Checked come only from the Myanmar side: the Myanmar check itself, the Myanmar checker or
    Owner / Admin (corrections). The Japan sender may only set Packed / Shipped – on every endpoint."""
    return status not in MYANMAR_STATUSES or can_check(user)


def is_myanmar_checker(user):
    """The Myanmar checker themself – not Owner / Admin, who can also check."""
    return _in_group(user, MYANMAR) and not is_manager(user)


def can_see_money(user):
    """Yen amounts, cargo fees and totals: Owner / Admin and the Japan sender – never the Myanmar checker."""
    return is_manager(user) or (_in_group(user, JAPAN) and not _in_group(user, MYANMAR))


def can_import_csv(user):
    """CSV import of Japan lists – Owner / Admin only."""
    return is_manager(user)


def can_use_requests(user):
    """Customer requests – Owner / Admin and the Myanmar checker (not the Japan sender)."""
    return is_manager(user) or can_check(user)


def sees_unshipped_boxes(user):
    """Packed (not yet shipped) boxes are hidden from the Myanmar checker."""
    return is_manager(user) or can_edit_japan(user)


def can_use_customers(user):
    """Customer list / details – same people as customer requests (not the Japan sender)."""
    return can_use_requests(user)


def can_use_payments(user):
    """Order prices (Ks), customer payments, balances and credit: Owner / Admin and the Myanmar checker –
    never the Japan sender."""
    return can_use_customers(user)


def can_edit_payments(user):
    """Delete / edit a recorded payment, and mark old orders as paid: Owner / Admin only."""
    return is_manager(user)


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
