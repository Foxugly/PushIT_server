import logging
import re
from dataclasses import dataclass, field
from email.utils import getaddresses
from html.parser import HTMLParser

import requests
from django.conf import settings
from msal import ConfidentialClientApplication

from . import inbound_alias

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
    # Raw Authentication-Results header value (SPF/DKIM/DMARC verdicts stamped by
    # M365 on receipt), or "" when not captured. Used to anti-spoof the `From`
    # when INBOUND_EMAIL_REQUIRE_DMARC is enabled. Empty unless the poller is
    # configured to fetch internetMessageHeaders.
    authentication_results: str = ""
    # Every recipient address seen (To, Cc, Delivered-To, X-Original-To,
    # Resent-To), lowercased, de-duplicated, in that order.
    recipients: tuple[str, ...] = ()
    # internetMessageHeaders, names lowercased (first occurrence wins). Used by
    # the auto-reply loop protection (Auto-Submitted, Precedence, List-Id...).
    headers: dict[str, str] = field(default_factory=dict)


# Headers that may carry the envelope recipient when it is not visible in
# To/Cc (Bcc, forwarding, list expansion). Lowercased.
RECIPIENT_HEADERS = ("delivered-to", "x-original-to", "resent-to")

_BLOCK_TAGS = frozenset({
    "address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre",
    "section", "table", "tr", "ul",
})
_SKIP_TAGS = frozenset({"script", "style", "head", "title", "noscript", "template"})


class _HTMLToText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def _break(self, lines: int = 1) -> None:
        """Ensure the text ends with at least ``lines`` line breaks."""
        tail = "".join(self._parts[-4:]).rstrip(" \t")
        missing = lines - (len(tail) - len(tail.rstrip("\n")))
        if self._parts and missing > 0:
            self._parts.append("\n" * missing)

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "br":
            self._parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self._break()
            if tag == "li":
                self._parts.append("- ")

    def handle_startendtag(self, tag, attrs):
        # <br/>, <hr/>: no matching end tag to wait for.
        if tag in _BLOCK_TAGS:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(self._skip_depth - 1, 0)
        elif tag in _BLOCK_TAGS and tag != "br":
            # A paragraph ends with a blank line, other blocks with a line break.
            self._break(2 if tag == "p" else 1)

    def handle_data(self, data):
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(content: str) -> str:
    """Stdlib HTML -> plain text: drops <script>/<style>/<head>, turns block
    tags and <br> into line breaks, unescapes entities, collapses whitespace and
    runs of blank lines. Good enough for a push notification body."""
    # html.parser never raises on malformed markup (it is lenient by design).
    parser = _HTMLToText()
    parser.feed(content or "")
    parser.close()
    # str.split() also treats &nbsp; (U+00A0) as whitespace.
    text = "\n".join(" ".join(line.split()) for line in parser.text().splitlines()).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def _recipient_candidates(msg: dict, headers: list[dict]) -> list[str]:
    seen: list[str] = []

    def add(address: str) -> None:
        address = inbound_alias.normalize_address(address)
        if address and "@" in address and address not in seen:
            seen.append(address)

    for field_name in ("toRecipients", "ccRecipients"):
        for entry in msg.get(field_name, []) or []:
            add((entry.get("emailAddress") or {}).get("address", ""))
    for wanted in RECIPIENT_HEADERS:
        values = [
            h.get("value") or ""
            for h in headers
            if (h.get("name") or "").strip().lower() == wanted
        ]
        for _, address in getaddresses(values):
            add(address)
    return seen


def pick_recipient(candidates: list[str], domain: str) -> str:
    """Prefer an alias-shaped address on the inbound domain; otherwise fall
    back to any address on that domain (so a rejection log still says where the
    mail went), else ""."""
    for address in candidates:
        if inbound_alias.is_inbound_alias_address(address):
            return address
    for address in candidates:
        if address.endswith(f"@{domain}"):
            return address
    return ""


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
            "$select": "id,from,toRecipients,ccRecipients,subject,body,internetMessageId,internetMessageHeaders",
            "$orderby": "receivedDateTime asc",
        },
        timeout=30,
    )
    r.raise_for_status()

    emails = []
    for msg in r.json().get("value", []):
        sender_addr = inbound_alias.normalize_address(((msg.get("from") or {}).get("emailAddress") or {}).get("address", ""))

        raw_headers = msg.get("internetMessageHeaders", []) or []
        header_map: dict[str, str] = {}
        for header in raw_headers:
            name = (header.get("name") or "").strip().lower()
            if name and name not in header_map:
                header_map[name] = (header.get("value") or "").strip()

        recipients = _recipient_candidates(msg, raw_headers)
        recipient_addr = pick_recipient(recipients, domain)

        body_content = msg.get("body", {}).get("content", "")
        content_type = msg.get("body", {}).get("contentType", "text")
        if content_type.lower() == "html":
            body_content = html_to_text(body_content)

        # M365 stamps SPF/DKIM/DMARC verdicts in Authentication-Results on
        # receipt. Capture it (may be absent for very old/migrated items) so the
        # serializer can anti-spoof the `From` when DMARC enforcement is enabled.
        auth_results = header_map.get("authentication-results", "")

        emails.append(GraphEmail(
            graph_id=msg["id"],
            sender=sender_addr,
            recipient=recipient_addr,
            subject=(msg.get("subject") or "").strip(),
            text=body_content.strip(),
            message_id=(msg.get("internetMessageId") or "").strip().strip("<>").strip(),
            authentication_results=auth_results,
            recipients=tuple(recipients),
            headers=header_map,
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
