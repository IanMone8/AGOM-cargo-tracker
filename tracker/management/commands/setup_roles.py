"""Create the role groups and (optionally) the four AGOM user accounts.

    python manage.py setup_roles                 # groups only
    python manage.py setup_roles --create-users  # groups + 4 users with random passwords

Safe to re-run: existing groups/users are left alone (passwords are not changed).
"""
import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from tracker import roles

USERS = [
    # username, group (None = superuser), is_staff
    ("admin", None, True),
    ("owner", roles.OWNER, True),
    ("japan", roles.JAPAN, False),
    ("myanmar", roles.MYANMAR, False),
]


class Command(BaseCommand):
    help = "Create role groups and optionally the four AGOM users."

    def add_arguments(self, parser):
        parser.add_argument("--create-users", action="store_true")

    def handle(self, *args, **opts):
        tracker_perms = Permission.objects.filter(content_type__app_label="tracker")
        for name in roles.ALL_GROUPS:
            group, created = Group.objects.get_or_create(name=name)
            if name == roles.OWNER:
                # Owner may also use the Django admin for the tracker data.
                group.permissions.set(tracker_perms)
            self.stdout.write(f"{'Created' if created else 'Found'} group {name}")

        if not opts["create_users"]:
            return

        User = get_user_model()
        for username, group_name, is_staff in USERS:
            if User.objects.filter(username=username).exists():
                self.stdout.write(f"User {username} already exists – skipped")
                continue
            password = secrets.token_urlsafe(9)
            if group_name is None:
                user = User.objects.create_superuser(username=username, password=password)
            else:
                user = User.objects.create_user(
                    username=username, password=password, is_staff=is_staff
                )
                user.groups.add(Group.objects.get(name=group_name))
            self.stdout.write(self.style.SUCCESS(
                f"Created {username:8} ({group_name or 'Admin'})  password: {password}"
            ))
        self.stdout.write("Change passwords at /admin/ or with: python manage.py changepassword <user>")
