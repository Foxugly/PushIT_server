from .base import *  # noqa: F401,F403

from django.core.exceptions import ImproperlyConfigured


DEBUG = False
STATE = "PROD"

SECRET_KEY = env("SECRET_KEY", default="").strip()
if not SECRET_KEY or SECRET_KEY == "dev-secret-key":
    raise ImproperlyConfigured("SECRET_KEY must be explicitly set in PROD.")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be explicitly set in PROD.")

# /health/metrics/ is never public in prod: without METRICS_AUTH_TOKEN it is
# denied (403), not open.
METRICS_REQUIRE_TOKEN = True

# Inbound e-mail: a forged `From` must not push to every subscriber (audit S-I4).
# Enforced unless SSM explicitly sets INBOUND_EMAIL_REQUIRE_DMARC=False.
INBOUND_EMAIL_REQUIRE_DMARC = env.bool("INBOUND_EMAIL_REQUIRE_DMARC", default=True)

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

