from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone
from rest_framework import authentication, exceptions

from config.metrics import increment_counter
from .models import Application


@dataclass(frozen=True)
class AppTokenPrincipal:
    pk: str
    application_id: int
    owner_id: int
    auth_kind: str = "application"

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_staff(self):
        return False

    @property
    def is_superuser(self):
        return False

    @property
    def is_app_token_principal(self):
        return True

    def get_username(self):
        return self.pk

    def __str__(self):
        return self.pk

    def __getattr__(self, name):
        raise AttributeError(
            f"AppTokenPrincipal does not expose '{name}'. "
            "Use request.auth_application for application data "
            "and do not treat app-token auth as a human user."
        )


class AppTokenAuthentication(authentication.BaseAuthentication):
    header_name = "HTTP_X_APP_TOKEN"

    def authenticate(self, request):
        raw_token = request.META.get(self.header_name)

        if not raw_token:
            increment_counter("pushit_app_token_auth_total", labels={"outcome": "missing"})
            raise exceptions.NotAuthenticated(
                "Missing app token.",
                code="app_token_missing",
            )

        if not raw_token.startswith("apt_"):
            increment_counter("pushit_app_token_auth_total", labels={"outcome": "invalid_format"})
            raise exceptions.AuthenticationFailed(
                "Invalid app token format.",
                code="app_token_invalid_format",
            )

        token_hash = Application.hash_app_token(raw_token)

        try:
            application = Application.objects.select_related("owner").get(app_token_hash=token_hash)
        except Application.DoesNotExist:
            increment_counter("pushit_app_token_auth_total", labels={"outcome": "invalid"})
            raise exceptions.AuthenticationFailed(
                "Invalid app token.",
                code="app_token_invalid",
            )

        if not application.is_active:
            increment_counter("pushit_app_token_auth_total", labels={"outcome": "inactive"})
            raise exceptions.AuthenticationFailed(
                "Inactive application.",
                code="app_token_inactive",
            )

        if application.revoked_at is not None:
            increment_counter("pushit_app_token_auth_total", labels={"outcome": "revoked"})
            raise exceptions.AuthenticationFailed(
                "App token has been revoked.",
                code="app_token_revoked",
            )

        now = timezone.now()
        if not application.last_used_at or application.last_used_at < now - timedelta(minutes=5):
            application.last_used_at = now
            application.save(update_fields=["last_used_at"])

        request.auth_application = application
        principal = AppTokenPrincipal(
            pk=f"app:{application.id}",
            application_id=application.id,
            owner_id=application.owner_id,
        )
        increment_counter("pushit_app_token_auth_total", labels={"outcome": "success"})
        return (principal, application)

    def authenticate_header(self, request):
        return "X-App-Token"
