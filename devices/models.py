from django.conf import settings
from django.db import models

from applications.models import Application, QuietPeriodType

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


class DeviceQuietPeriod(models.Model):
    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name="quiet_periods",
    )
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
        ordering = ["id"]

    def __str__(self):
        if self.period_type == QuietPeriodType.ONCE and self.start_at is not None:
            return self.name or f"device-quiet:{self.device_id}:{self.start_at.isoformat()}"
        return self.name or f"device-quiet:{self.device_id}:{self.period_type.lower()}"

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
