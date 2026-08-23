"""
Django settings for the EmailPro pilot project.

All secrets and environment-specific values are read from environment
variables (optionally loaded from a local .env file). Nothing sensitive
is hard-coded here.
"""
from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load a local .env file if present (harmless in production where real
# environment variables are set by the host instead).
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "core",
    "leads",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "emailpro.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "emailpro.wsgi.application"
ASGI_APPLICATION = "emailpro.asgi.application"

# --- Database -----------------------------------------------------------
# SQLite by default for the local pilot. Set DATABASE_URL to switch to
# Postgres (or anything else dj-database-url supports) later without any
# code changes.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL:
    import dj_database_url

    DATABASES = {
        "default": dj_database_url.parse(DATABASE_URL, conn_max_age=600)
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "core" / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/"

# --- Celery / Redis -------------------------------------------------------
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 60  # 1 hour ceiling for a send/classify job

# --- EmailPro pilot configuration (all via environment only) --------------
# These are intentionally NOT given hard-coded defaults for secrets: an
# empty value means "not configured yet" and is surfaced as such on the
# Settings screen, never as a working fallback credential.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash").strip()

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
SENDER_NAME = os.environ.get("SENDER_NAME", "EmailPro Pilot").strip()

try:
    SEND_RATE_PER_MINUTE = max(1, int(os.environ.get("SEND_RATE_PER_MINUTE", "20")))
except ValueError:
    SEND_RATE_PER_MINUTE = 20

try: 
    LEADS_SEND_RATE_PER_MINUTE = max(1, int(os.environ.get("LEADS_SEND_RATE_PER_MINUTE", "20"))) 
except ValueError: 
    LEADS_SEND_RATE_PER_MINUTE = 20 
    SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "http://localhost:8000").strip()

# Django's own email backend is configured from the same Gmail SMTP
# credentials so we can use EmailMultiAlternatives/send_mail directly.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 465
EMAIL_USE_SSL = True
EMAIL_HOST_USER = GMAIL_ADDRESS
EMAIL_HOST_PASSWORD = GMAIL_APP_PASSWORD
DEFAULT_FROM_EMAIL = f"{SENDER_NAME} <{GMAIL_ADDRESS}>" if GMAIL_ADDRESS else GMAIL_ADDRESS

MAX_ATTACHMENT_SIZE_MB = 10
