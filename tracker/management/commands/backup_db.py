"""Copy the SQLite database to a dated file and keep only the newest ones.

    python manage.py backup_db                 # -> <BACKUP_DIR>/agom-2026-10-18-093000.sqlite3, keeps the last 14
    python manage.py backup_db --keep 30

BACKUP_DIR (env / .env) defaults to a "backups" folder next to the database file. The copy uses SQLite's online
backup, so it is consistent even while people are using the site. Run it by hand, or as a daily scheduled task.
"""
import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

PREFIX, SUFFIX = "agom-", ".sqlite3"


class Command(BaseCommand):
    help = "Back up the SQLite database to a dated file in the backups folder (keeps the newest 14)."

    def add_arguments(self, parser):
        parser.add_argument("--keep", type=int, default=14, help="How many backups to keep (default 14).")
        parser.add_argument("--dir", default=None, help="Backups folder (default: settings.BACKUP_DIR).")
        parser.add_argument("--source", default=None, help="Database file (default: the site's SQLite database).")

    def handle(self, *args, keep, dir, source, **opts):
        db = settings.DATABASES["default"]
        if not source and db["ENGINE"] != "django.db.backends.sqlite3":
            raise CommandError("backup_db only backs up SQLite databases.")
        source = Path(source or db["NAME"])
        if not source.is_file():
            raise CommandError(f"Database file not found: {source}")
        if keep < 1:
            raise CommandError("--keep must be at least 1.")
        folder = Path(dir or settings.BACKUP_DIR)
        folder.mkdir(parents=True, exist_ok=True)

        stamp = timezone.now().astimezone(timezone.get_default_timezone()).strftime("%Y-%m-%d-%H%M%S")
        target = folder / f"{PREFIX}{stamp}{SUFFIX}"
        src, dst = sqlite3.connect(source), sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        self.stdout.write(self.style.SUCCESS(f"Backup saved: {target} ({target.stat().st_size // 1024} KB)"))

        backups = sorted(folder.glob(f"{PREFIX}*{SUFFIX}"), key=lambda p: p.name, reverse=True)
        for old in backups[keep:]:
            old.unlink()
            self.stdout.write(f"Removed old backup: {old.name}")
