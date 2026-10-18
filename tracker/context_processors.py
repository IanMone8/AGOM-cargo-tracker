from . import roles as r
from .models import user_time_zone, zone_label


def roles(request):
    user = request.user
    if not user.is_authenticated:
        return {}
    return {
        "role_name": r.role_name(user),
        "zone_label": zone_label(user_time_zone(user)),          # user card: "Japan Sender · JST"
        "can_edit_japan": r.can_edit_japan(user),
        "can_check": r.can_check(user),
        "is_manager": r.is_manager(user),
        "can_see_money": r.can_see_money(user),
        "can_import_csv": r.can_import_csv(user),
        "can_use_requests": r.can_use_requests(user),
        "can_use_customers": r.can_use_customers(user),
        "can_use_payments": r.can_use_payments(user),
        "can_edit_payments": r.can_edit_payments(user),
    }
