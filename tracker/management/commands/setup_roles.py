"""Create the four role groups if they are missing: Admin, Owner, Japan Sender, Myanmar Checker.

    python manage.py setup_roles

Safe to re-run (run on every deploy): existing groups are kept. No users or passwords are created here –
make people with `python manage.py createsuperuser` or in /admin/, then add each one to their group.
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from tracker import roles


class Command(BaseCommand):
    help = "Create the role groups (Admin, Owner, Japan Sender, Myanmar Checker) if missing."

    def handle(self, *args, **opts):
        tracker_perms = Permission.objects.filter(content_type__app_label="tracker")
        for name in roles.ALL_GROUPS:
            group, created = Group.objects.get_or_create(name=name)
            if name in (roles.OWNER, roles.ADMIN):
                # Owner / Admin may also use the Django admin for the tracker data (with "staff status").
                group.permissions.set(tracker_perms)
            self.stdout.write(f"{'Created' if created else 'Found'} group {name}")
