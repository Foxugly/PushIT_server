from applications.models import Application

from .models import InboundEmailIngestionLog, Notification


def record_inbound_email_ingestion(
    *,
    source: str,
    status: str,
    sender: str = "",
    recipient: str = "",
    subject: str = "",
    message_id: str = "",
    mailbox_uid: str = "",
    scheduled_for=None,
    application: Application | None = None,
    notification: Notification | None = None,
    error_message: str = "",
) -> InboundEmailIngestionLog:
    return InboundEmailIngestionLog.objects.create(
        source=source,
        status=status,
        application=application,
        notification=notification,
        sender=sender,
        recipient=recipient,
        subject=subject[:255],
        message_id=message_id,
        mailbox_uid=mailbox_uid,
        scheduled_for=scheduled_for,
        error_message=error_message,
    )
