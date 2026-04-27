import os
import ssl
from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="django-insecure-dev-key-change-in-prod")
DEBUG = env("DEBUG", default=True)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "payouts",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves static files in prod
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend_dist"],
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

WSGI_APPLICATION = "config.wsgi.application"

import dj_database_url

# Allow test runner to override DB (set TESTING=1 to use SQLite)
if os.environ.get("TESTING") == "1":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }
else:
    # 1. Inspect Environment
    db_url = os.environ.get("DATABASE_URL", "").strip().strip("'\"")
    print(f"DEBUG: Available ENV Keys: {list(os.environ.keys())}")
    
    if not db_url:
        print("CRITICAL: DATABASE_URL is EMPTY or NOT FOUND in environment.")
        # Fallback to local default if specifically requested, but log it loudly
        db_url = "postgresql://postgres:postgres@db:5432/playto"
        print(f"DEBUG: Falling back to internal default: {db_url}")
    else:
        # Masked print for security
        masked = f"{db_url[:15]}...{db_url[-5:]}" if len(db_url) > 20 else "***"
        print(f"DEBUG: DATABASE_URL found (length {len(db_url)}): {masked}")

    DATABASES = {
        "default": dj_database_url.parse(db_url)
    }
    DATABASES["default"]["CONN_MAX_AGE"] = 600

    # Verify if parsing actually worked
    if not DATABASES["default"].get("ENGINE"):
        print("CRITICAL: dj_database_url failed to parse DATABASE_URL (ENGINE is missing).")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "frontend_dist",
]

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "payouts.exceptions.custom_exception_handler",
}

# CORS — in dev allow all origins; in prod restrict to CORS_ALLOWED_ORIGINS env var
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=DEBUG)
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
)
CORS_ALLOW_HEADERS = ["content-type", "authorization", "idempotency-key", "x-request-id"]
CORS_ALLOW_METHODS = ["GET", "POST", "OPTIONS"]  # only what our API needs

# Celery
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")

# Fix for rediss:// (SSL) required by some providers like Upstash/Railway
if CELERY_BROKER_URL.startswith("rediss://"):
    CELERY_REDIS_BACKEND_USE_SSL = {
        "ssl_cert_reqs": ssl.CERT_NONE,
    }
    BROKER_USE_SSL = {
        "ssl_cert_reqs": ssl.CERT_NONE,
    }

CELERY_TIMEZONE = "Asia/Kolkata"
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# Payout config
MAX_PAYOUT_LIMIT_PAISE = 10_000_000  # 1 lakh INR = 1,00,00,000 paise
