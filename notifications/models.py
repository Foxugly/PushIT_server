from django.db import models

from applications.models import Application
from devices.models import Device

class DeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"

class NotificationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SCHEDULED = "scheduled", "Scheduled"
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    PARTIAL = "partial", "Partial"
    NO_TARGET = "no_target", "No target"


class InboundEmailSource(models.TextChoices):
    IMAP = "imap", "IMAP"
    WEBHOOK = "webhook", "Webhook"


class InboundEmailIngestionStatus(models.TextChoices):
    CREATED = "created", "Created"
    EXISTING = "existing", "Existing"
    REJECTED = "rejected", "Rejected"
    CONFLICT = "conflict", "Conflict"
    ERROR = "error", "Error"


# Create your models here.
class Notification(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="notifications",)
    title = models.CharField(max_length=255)
    message = models.TextField()
    image = models.ImageField(upload_to="notification_images/", blank=True, null=True)
    status = models.CharField(max_length=20, choices=NotificationStatus.choices, default=NotificationStatus.DRAFT,)
    created_at = models.DateTimeField(auto_now_add=True)
    scheduled_for = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    idempotency_key = models.CharField(max_length=255, blank=True, db_index=True)
    request_fingerprint = models.CharField(max_length=64, blank=True)
    
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["application", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_notification_app_idempotency_key",
            )
        ]

    def __str__(self):
        return self.title

class NotificationDelivery(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="deliveries",)
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="deliveries",)
    status = models.CharField(
        max_length=20,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
    )
    provider_message_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "device"],
                name="uniq_notification_device",
            )
        ]


class InboundEmailIngestionLog(models.Model):
    source = models.CharField(max_length=20, choices=InboundEmailSource.choices)
    status = models.CharField(max_length=20, choices=InboundEmailIngestionStatus.choices, db_index=True)
    application = models.ForeignKey(
        Application,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_email_logs",
    )
    notification = models.ForeignKey(
        Notification,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_email_logs",
    )
    sender = models.EmailField(blank=True)
    recipient = models.EmailField(blank=True)
    subject = models.CharField(max_length=255, blank=True)
    message_id = models.CharField(max_length=255, blank=True, db_index=True)
    mailbox_uid = models.CharField(max_length=255, blank=True, db_index=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-processed_at", "-id"]

    def __str__(self):
        return f"{self.source}:{self.status}:{self.recipient or 'unknown'}"
