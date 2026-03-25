from django.conf import settings
from django.db import models

from applications.models import Application

class DeviceTokenStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INVALID = "invalid", "Invalid"
    REVOKED = "revoked", "Revoked"

class DevicePlatform(models.TextChoices):
    ANDROID = "android", "Android"
    IOS = "ios", "iOS"


class Device(models.Model):
    #user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="devices",)
    device_name = models.CharField(max_length=120, blank=True)
    platform = models.CharField(max_length=20, choices=DevicePlatform.choices, default=DevicePlatform.ANDROID)
    push_token = models.CharField(max_length=512, unique=True)
    push_token_status = models.CharField(max_length=20, choices=DeviceTokenStatus.choices, default=DeviceTokenStatus.ACTIVE)
    last_seen_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidation_reason = models.CharField(max_length=100, blank=True)
    failure_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.device_name or self.push_token[:16]} ({self.platform})"

class DeviceApplicationLink(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="application_links")
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="device_links")
    is_active = models.BooleanField(default=True)
    linked_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["device", "application"],
                name="uniq_device_application",
            )
        ]

    def __str__(self):
        return f"device={self.device_id} <-> app={self.application_id}"