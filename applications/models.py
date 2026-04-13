import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class QuietPeriodType(models.TextChoices):
    ONCE = "ONCE", "One-time"
    RECURRING = "RECURRING", "Recurring"


def generate_raw_app_token():
    return f"apt_{secrets.token_hex(24)}"


def generate_inbound_email_alias():
    return f"apt_{secrets.token_hex(8)}"


def hash_app_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def get_token_prefix(raw_token: str, visible_length: int = 12) -> str:
    return raw_token[:visible_length]


class Application(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="applications")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    app_token_prefix = models.CharField(max_length=24, db_index=True)
    app_token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    inbound_email_alias = models.CharField(max_length=24, unique=True, db_index=True)

    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    icon = models.ImageField(upload_to="app_icons/", blank=True, null=True)
    logo = models.ImageField(upload_to="app_logo/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def inbound_email_address(self) -> str:
        return f"{self.inbound_email_alias}@{settings.INBOUND_EMAIL_DOMAIN.strip().lower()}"

    def check_app_token(self, raw_token: str) -> bool:
        return self.app_token_hash == hash_app_token(raw_token)

    def revoke_token(self, save: bool = True):
        self.revoked_at = timezone.now()
        if save:
            self.save(update_fields=["revoked_at"])

    def mark_token_used(self, save: bool = True):
        self.last_used_at = timezone.now()
        if save:
            self.save(update_fields=["last_used_at"])

    def set_new_app_token(self) -> str:
        raw_token = generate_raw_app_token()
        self.app_token_prefix = get_token_prefix(raw_token)
        self.app_token_hash = hash_app_token(raw_token)
        self.revoked_at = None
        self.last_used_at = None
        return raw_token

    def save(self, *args, **kwargs):
        if not self.app_token_hash:
            self.set_new_app_token()
        if not self.inbound_email_alias:
            while True:
                candidate = generate_inbound_email_alias()
                if not type(self).objects.filter(inbound_email_alias=candidate).exists():
                    self.inbound_email_alias = candidate
                    break
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.owner})"


class AbstractQuietPeriod(models.Model):
    name = models.CharField(max_length=120, blank=True)
    period_type = models.CharField(max_length=16, choices=QuietPeriodType.choices, default=QuietPeriodType.ONCE)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    recurrence_days = models.JSONField(default=list, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["id"]


class ApplicationQuietPeriod(AbstractQuietPeriod):
    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="quiet_periods",
    )

    def __str__(self):
        if self.period_type == QuietPeriodType.ONCE and self.start_at is not None:
            return self.name or f"quiet:{self.application_id}:{self.start_at.isoformat()}"
        return self.name or f"quiet:{self.application_id}:{self.period_type.lower()}"
