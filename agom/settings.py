"""Django settings for AGOM Cargo Tracker.

Configuration comes from environment variables, so the same code runs locally (no settings needed: DEBUG on,
SQLite in the project folder) and online. On the server the variables live in a `.env` file next to manage.py
(or at the path in AGOM_ENV_FILE) – it is git-ignored and never committed. See DEPLOY_PYTHONANYWHERE.md.
"""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path):
    """KEY=value lines from a .env file into os.environ (real environment variables win). # comments and blank
    lines are skipped; values may be wrapped in quotes. No extra package needed."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(os.environ.get("AGOM_ENV_FILE", BASE_DIR / ".env"))


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


# PythonAnywhere sets PYTHONANYWHERE_DOMAIN on its servers: there DEBUG is off unless DEBUG=true is set on purpose.
# Locally (no .env) everything works as before: DEBUG on, SQLite in the project folder, no HTTPS.
ON_PYTHONANYWHERE = bool(os.environ.get("PYTHONANYWHERE_DOMAIN"))
DEBUG = env_bool("DEBUG", not ON_PYTHONANYWHERE)
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me" if DEBUG else "")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is required when DEBUG is off.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "tracker",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "tracker.middleware.UserTimeZoneMiddleware",           # dates / times shown in each user's own zone
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "agom.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tracker.context_processors.roles",
            ],
        },
    },
]

WSGI_APPLICATION = "agom.wsgi.application"

# SQLite. Online, SQLITE_PATH puts the database file OUTSIDE the git folder (e.g. /home/agom/data/agom.sqlite3),
# so `git pull` never touches the data. Locally: db.sqlite3 in the project folder, as before.
# (DATABASE_URL still works for another database, e.g. postgres://user:pass@host:5432/dbname.)
SQLITE_PATH = os.environ.get("SQLITE_PATH", str(BASE_DIR / "db.sqlite3"))
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{SQLITE_PATH}",
        conn_max_age=int(os.environ.get("CONN_MAX_AGE", "0")),
        conn_health_checks=True,
    )
}
BACKUP_DIR = os.environ.get("BACKUP_DIR", str(Path(SQLITE_PATH).parent / "backups"))   # manage.py backup_db

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
# The site's business time zone: order dates and date rules use it (Box IDs use Japan time). What each user SEES
# is their own zone (tracker.middleware.UserTimeZoneMiddleware, UserProfile.time_zone).
TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Yangon")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# collectstatic copies everything here; PythonAnywhere serves it (Web tab: URL /static/ -> this folder).
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", BASE_DIR / "staticfiles"))
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "agom.storage.StaticStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

if not DEBUG:
    # HTTPS only (PythonAnywhere ends TLS at its front end and passes X-Forwarded-Proto; also tick "Force HTTPS")
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", str(60 * 60 * 24 * 365)))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", True)
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
