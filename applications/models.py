import hashlib
import hmac
import re
import secrets

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.text import slugify

from .url_safety import validate_webhook_url


class QuietPeriodType(models.TextChoices):
    ONCE = "ONCE", "One-time"
    RECURRING = "RECURRING", "Recurring"


class Application(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="applications")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    app_token_prefix = models.CharField(max_length=24, db_index=True)
    app_token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    inbound_email_alias = models.CharField(max_length=120, unique=True, db_index=True)
    # The random suffix of the alias, stored + DB-unique so it's race-proof and
    # queryable (the alias is "app_<slug>_<suffix>"). Populated on save().
    inbound_email_suffix = models.CharField(max_length=32, unique=True)
    webhook_url = models.URLField(max_length=500, blank=True, validators=[validate_webhook_url])

    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    icon = models.ImageField(upload_to="app_icons/", blank=True, null=True)
    logo = models.ImageField(upload_to="app_logo/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def inbound_email_address(self) -> str:
        return f"{self.inbound_email_alias}@{settings.INBOUND_EMAIL_DOMAIN.strip().lower()}"

    @staticmethod
    def hash_app_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def get_token_prefix(raw_token: str, visible_length: int = 12) -> str:
        return raw_token[:visible_length]

    @staticmethod
    def generate_raw_app_token() -> str:
        return f"apt_{secrets.token_hex(24)}"

    # Inbound-alias format: "app_<name-slug>_<random>", e.g. app_my_resto_3f9a2c1b.
    # The random suffix makes the address unique AND non-guessable (so the inbound
    # endpoint can't be spammed by guessing app_<name>@domain). Underscore-separated
    # to match the app_/apt_ token convention.
    ALIAS_PREFIX = "app_"
    ALIAS_SUFFIX_BYTES = 4  # -> 8 hex chars

    @staticmethod
    def generate_inbound_email_alias(name: str) -> str:
        slug = slugify(name).replace("-", "_")
        slug = re.sub(r"_+", "_", slug).strip("_")
        suffix = secrets.token_hex(Application.ALIAS_SUFFIX_BYTES)
        base = f"{Application.ALIAS_PREFIX}{slug}" if slug else Application.ALIAS_PREFIX.rstrip("_")
        # Keep the whole alias within the field's 120 chars (base + "_" + suffix).
        base = base[: 120 - 1 - len(suffix)].strip("_") or Application.ALIAS_PREFIX.rstrip("_")
        return f"{base}_{suffix}"

    @staticmethod
    def _suffix_of(alias: str) -> str:
        """The random suffix is always the final `_`-segment of the alias."""
        return alias.rsplit("_", 1)[-1]

    def check_app_token(self, raw_token: str) -> bool:
        # Constant-time compare of the stored hash vs the candidate hash, to avoid
        # leaking a match via timing. Both operands are fixed-length sha256 hex.
        return hmac.compare_digest(self.app_token_hash, self.hash_app_token(raw_token))

    def revoke_token(self, save: bool = True):
        self.revoked_at = timezone.now()
        if save:
            self.save(update_fields=["revoked_at"])

    def mark_token_used(self, save: bool = True):
        self.last_used_at = timezone.now()
        if save:
            self.save(update_fields=["last_used_at"])

    def set_new_app_token(self) -> str:
        raw_token = self.generate_raw_app_token()
        self.app_token_prefix = self.get_token_prefix(raw_token)
        self.app_token_hash = self.hash_app_token(raw_token)
        self.revoked_at = None
        self.last_used_at = None
        return raw_token

    def save(self, *args, **kwargs):
        if not self.app_token_hash:
            self.set_new_app_token()

        if self.inbound_email_alias:
            # Alias already assigned (update path): keep the stored suffix in sync.
            if not self.inbound_email_suffix:
                self.inbound_email_suffix = self._suffix_of(self.inbound_email_alias)
            super().save(*args, **kwargs)
            return

        # New alias: allocate a unique one. The DB UNIQUE constraint on the suffix
        # is the source of truth (race-proof); on the astronomically rare collision
        # we just regenerate and retry — no app-level pre-check needed.
        last_error: IntegrityError | None = None
        for _ in range(12):
            alias = self.generate_inbound_email_alias(self.name)
            self.inbound_email_alias = alias
            self.inbound_email_suffix = self._suffix_of(alias)
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
            except IntegrityError as exc:
                last_error = exc
                self.inbound_email_alias = ""
                self.inbound_email_suffix = ""
                continue
            self._provision_exchange_alias()
            return
        raise last_error

    def regenerate_inbound_email(self) -> None:
        """Allocate a fresh inbound alias/suffix (a brand-new ingestion address)
        and re-provision Exchange: clearing the alias makes save() reallocate a
        unique alias+suffix and provision the new Exchange alias; the old alias is
        then deprovisioned. The previous address stops working afterwards."""
        old_local = self.inbound_email_alias
        self.inbound_email_alias = ""
        self.inbound_email_suffix = ""
        self.save()
        if old_local and old_local != self.inbound_email_alias:
            self._deprovision_exchange_alias(old_local)

    def delete(self, *args, **kwargs):
        alias = self.inbound_email_alias
        result = super().delete(*args, **kwargs)
        if alias:
            self._deprovision_exchange_alias(alias)
        return result

    def _provision_exchange_alias(self) -> None:
        from exchange.integration import provision_alias_for_application
        provision_alias_for_application(self.inbound_email_address)

    def _deprovision_exchange_alias(self, alias_local_part: str) -> None:
        from exchange.integration import deprovision_alias_for_application
        from django.conf import settings
        deprovision_alias_for_application(f"{alias_local_part}@{settings.INBOUND_EMAIL_DOMAIN}")

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
