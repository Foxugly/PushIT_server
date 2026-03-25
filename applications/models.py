import hashlib
import secrets
from django.conf import settings
from django.db import models
from django.utils import timezone

def generate_raw_app_token():
    return f"apt_{secrets.token_hex(24)}"


def hash_app_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def get_token_prefix(raw_token: str, visible_length: int = 12) -> str:
    return raw_token[:visible_length]

# Create your models here.
class Application(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="applications",)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    app_token_prefix = models.CharField(max_length=24, db_index=True)
    app_token_hash = models.CharField(max_length=64, unique=True, db_index=True)

    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    icon = models.ImageField(upload_to="app_icons/", blank=True, null=True)
    logo = models.ImageField(upload_to="app_logo/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

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
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.owner})"