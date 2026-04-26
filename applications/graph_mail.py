import logging
from dataclasses import dataclass

import requests
from django.conf import settings
from msal import ConfidentialClientApplication

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_msal_app = None
_msal_tenant = None


def _get_msal_app():
    global _msal_app, _msal_tenant
    tenant = settings.GRAPH_TENANT_ID
    if _msal_app is None or _msal_tenant != tenant:
        _msal_app = ConfidentialClientApplication(
            settings.GRAPH_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{tenant}",
            client_credential=settings.GRAPH_CLIENT_SECRET,
        )
        _msal_tenant = tenant
    return _msal_app


def _is_configured() -> bool:
    return bool(getattr(settings, "GRAPH_CLIENT_ID", ""))


def _get_access_token() -> str:
    app = _get_msal_app()
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Failed to acquire Graph API token: {result.get('error_description', result)}")
    return result["access_token"]


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_access_token()}",
        "Content-Type": "application/json",
    }


def _user_url(path: str = "") -> str:
    user_id = settings.GRAPH_MAILBOX_USER_ID
    return f"{GRAPH_BASE}/users/{user_id}{path}"


# ---------------------------------------------------------------------------
# Inbox polling (replaces IMAP)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GraphEmail:
    graph_id: str
    sender: str
    recipient: str
    subject: str
    text: str
    message_id: str


def fetch_unread_emails(max_count: int = 50) -> list[GraphEmail]:
    if not _is_configured():
        return []

    headers = _headers()
    domain = settings.INBOUND_EMAIL_DOMAIN.strip().lower()

    r = requests.get(
        _user_url("/mailFolders/Inbox/messages"),
        headers=headers,
        params={
            "$filter": "isRead eq false",
            "$top": max_count,
            "$select": "id,from,toRecipients,ccRecipients,subject,body,internetMessageId",
            "$orderby": "receivedDateTime asc",
        },
        timeout=30,
    )
    r.raise_for_status()

    emails = []
    for msg in r.json().get("value", []):
        sender_addr = (msg.get("from", {}).get("emailAddress", {}).get("address", "")).strip().lower()

        recipient_addr = ""
        for field in ("toRecipients", "ccRecipients"):
            for entry in msg.get(field, []):
                addr = (entry.get("emailAddress", {}).get("address", "")).strip().lower()
                if addr.endswith(f"@{domain}"):
                    recipient_addr = addr
                    break
            if recipient_addr:
                break

        body_content = msg.get("body", {}).get("content", "")
        content_type = msg.get("body", {}).get("contentType", "text")
        if content_type.lower() == "html":
            import re
            body_content = re.sub(r"<[^>]+>", " ", body_content)
            body_content = " ".join(body_content.split())

        emails.append(GraphEmail(
            graph_id=msg["id"],
            sender=sender_addr,
            recipient=recipient_addr,
            subject=(msg.get("subject") or "").strip(),
            text=body_content.strip(),
            message_id=(msg.get("internetMessageId") or "").strip().strip("<>").strip(),
        ))

    return emails


def mark_email_read(graph_id: str) -> None:
    if not _is_configured():
        return

    try:
        requests.patch(
            _user_url(f"/messages/{graph_id}"),
            headers=_headers(),
            json={"isRead": True},
            timeout=30,
        ).raise_for_status()
    except Exception:
        logger.exception("graph_mail_mark_read_failed", extra={"graph_id": graph_id})


# ---------------------------------------------------------------------------
# Send reply email
# ---------------------------------------------------------------------------

def send_email(to: str, subject: str, body: str) -> None:
    if not _is_configured():
        logger.warning("graph_mail_skipped", extra={"reason": "not configured", "to": to})
        return

    try:
        requests.post(
            _user_url("/sendMail"),
            headers=_headers(),
            json={
                "message": {
                    "subject": subject,
                    "body": {
                        "contentType": "Text",
                        "content": body,
                    },
                    "toRecipients": [
                        {"emailAddress": {"address": to}},
                    ],
                },
                "saveToSentItems": False,
            },
            timeout=30,
        ).raise_for_status()
        logger.info("graph_mail_sent", extra={"to": to, "subject": subject})
    except Exception:
        logger.exception("graph_mail_send_failed", extra={"to": to, "subject": subject})
