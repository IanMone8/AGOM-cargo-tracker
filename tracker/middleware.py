from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import user_time_zone


class UserTimeZoneMiddleware:
    """Show dates and times in the signed-in user's own zone (UserProfile.time_zone, or their role's default).
    Display only: business rules use fixed zones (models.box_period: Japan time, yangon_today / site_today:
    Myanmar time), so they don't change with the viewer."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            timezone.activate(ZoneInfo(user_time_zone(user)))
        else:
            timezone.deactivate()
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
