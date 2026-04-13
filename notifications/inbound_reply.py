import logging

from applications.graph_mail import send_email

logger = logging.getLogger(__name__)


def build_unknown_address_reply(sender_email: str, tried_recipient: str) -> tuple[str, str]:
    from accounts.models import User
    from applications.models import Application

    user = User.objects.filter(email=sender_email).first()
    if user is None:
        return "", ""

    apps = Application.objects.filter(
        owner=user,
        is_active=True,
        revoked_at__isnull=True,
    ).order_by("name")

    if not apps.exists():
        body = (
            f"Your email to {tried_recipient} could not be delivered.\n\n"
            "You don't have any active applications configured.\n"
            "Please create an application first on PushIT."
        )
        return "Undeliverable: no active application", body

    lines = [
        f"Your email to {tried_recipient} could not be delivered "
        "because this address does not match any of your applications.",
        "",
        "Here are your valid inbound email addresses:",
        "",
    ]
    for app in apps:
        lines.append(f"  - {app.name}: {app.inbound_email_address}")

    lines.append("")
    lines.append("Please resend your email to the correct address.")

    return "Undeliverable: unknown recipient address", "\n".join(lines)


def send_unknown_address_reply(sender_email: str, tried_recipient: str) -> None:
    subject, body = build_unknown_address_reply(sender_email, tried_recipient)
    if subject and body:
        send_email(to=sender_email, subject=subject, body=body)
        logger.info(
            "inbound_email_unknown_address_reply_sent",
            extra={"sender": sender_email, "recipient": tried_recipient},
        )
