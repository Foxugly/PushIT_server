from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab


BASE_DIR = Path(__file__).resolve().parents[2]
env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")
SQLITE_NAME = Path(env("SQLITE_NAME", default=str(BASE_DIR / "db.sqlite3")))

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-secret-key")
STATE = env("STATE", default="DEV")
DEBUG = env.bool("DEBUG", default=False)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "10.0.2.2"])
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:4200", "http://127.0.0.1:4200"],
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "rest_framework_simplejwt.token_blacklist",
    "accounts.apps.AccountsConfig",
    "applications.apps.ApplicationsConfig",
    "devices.apps.DevicesConfig",
    "notifications.apps.NotificationsConfig",
    "health.apps.HealthConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "config.middleware.RequestIdMiddleware",
    "config.middleware.MetricsMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": env("DATABASE_ENGINE", default="django.db.backends.sqlite3"),
        "NAME": env("DATABASE_NAME", default=str(SQLITE_NAME)),
        "HOST": env("DATABASE_HOST", default=""),
        "PORT": env("DATABASE_PORT", default=""),
        "USER": env("DATABASE_USER", default=""),
        "PASSWORD": env("DATABASE_PASSWORD", default=""),
    }
}

DB_SUPPORTS_ROW_LOCKING = "sqlite" not in DATABASES["default"]["ENGINE"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "accounts.auth_backend.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Brussels"
USE_I18N = True
USE_TZ = True

AUTH_USER_MODEL = "accounts.User"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / env("MEDIA_ROOT_DIR")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {
            "()": "config.logging_utils.RequestIdFilter",
        },
    },
    "formatters": {
        "json": {
            "()": "config.logging_utils.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_id"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

CELERY_BROKER_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", default="redis://127.0.0.1:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Europe/Brussels"
CELERY_BEAT_SCHEDULE = {
    "pushit-dispatch-scheduled-notifications": {
        "task": "notifications.tasks.dispatch_scheduled_notifications_task",
        "schedule": crontab(minute="*"),
    },
    "pushit-retry-pending-deliveries": {
        "task": "notifications.tasks.retry_pending_deliveries_task",
        "schedule": crontab(minute="*"),
    },
    "pushit-poll-inbound-mailbox": {
        "task": "notifications.tasks.poll_inbound_mailbox_task",
        "schedule": crontab(minute="*"),
    },
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min",
        "user": "120/min",
        "login": "10/min",
        "register": "5/min",
        "app_token": "300/min",
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "config.exceptions.custom_exception_handler",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=1)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

FCM_API_KEY = env("FCM_API_KEY", default=None)
FCM_SERVICE_ACCOUNT_PATH = env("FCM_SERVICE_ACCOUNT_PATH", default="")
METRICS_AUTH_TOKEN = env("METRICS_AUTH_TOKEN", default=None)
INBOUND_EMAIL_DOMAIN = env("INBOUND_EMAIL_DOMAIN", default="pushit.com")
INBOUND_EMAIL_SECRET = env("INBOUND_EMAIL_SECRET", default="dev-inbound-email-secret")

GRAPH_TENANT_ID = env("GRAPH_TENANT_ID", default="")
GRAPH_CLIENT_ID = env("GRAPH_CLIENT_ID", default="")
GRAPH_CLIENT_SECRET = env("GRAPH_CLIENT_SECRET", default="")
GRAPH_MAILBOX_USER_ID = env("GRAPH_MAILBOX_USER_ID", default="")

SPECTACULAR_SETTINGS = {
    "TITLE": "PushIT API",
    "DESCRIPTION": "Push notification API backend for Firebase Cloud Messaging",
    "VERSION": "1.0.0",
    "SECURITY": [],
    "ENUM_NAME_OVERRIDES": {
        "DevicePlatformEnum": "devices.models.DevicePlatform",
    },
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENTS": {
        "securitySchemes": {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            },
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-App-Token",
            },
        }
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
