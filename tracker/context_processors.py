from . import roles as r


def roles(request):
    user = request.user
    if not user.is_authenticated:
        return {}
    return {
        "role_name": r.role_name(user),
        "can_edit_japan": r.can_edit_japan(user),
        "can_check": r.can_check(user),
        "is_manager": r.is_manager(user),
    }
