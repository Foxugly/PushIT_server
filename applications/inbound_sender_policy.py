"""Per-application policy: who may create notifications by e-mail.

An application's inbound alias accepts mail according to:

- ``inbound_sender_mode`` = ``owner`` (default): the owner's own address, plus
  any sender matching ``inbound_sender_allowlist``;
- ``inbound_sender_mode`` = ``anyone``: every sender.

``inbound_sender_blocklist`` applies in both modes and wins over the allowlist,
but never blocks the owner's own address (the owner cannot lock themselves out
by listing their own domain).

List entries are one of:

- an exact address (``alice@example.com``);
- a domain (``@example.com``) -- exact domain match, subdomains are NOT
  included;
- a wildcard domain (``@*.example.com``) -- the domain itself AND any of its
  subdomains (``a@example.com``, ``a@mail.example.com``), never a look-alike
  (``a@badexample.com``). ``*`` is only accepted as the whole first label,
  right after the ``@``.

They are stored lowercase, deduplicated, at most ``MAX_SENDER_LIST_ENTRIES``.

This decides *policy* only. DMARC/anti-spoofing is evaluated afterwards on every
sender the policy accepts (see notifications.serializers).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

MODE_OWNER = "owner"
MODE_ANYONE = "anyone"
MODE_CHOICES = [(MODE_OWNER, "Owner (and allowlist) only"), (MODE_ANYONE, "Anyone")]

MAX_SENDER_LIST_ENTRIES = 200
MAX_SENDER_ENTRY_LENGTH = 254
WILDCARD_DOMAIN_PREFIX = "@*."

# Refusal reasons (journalled, shown to the owner in the notice).
REASON_BLOCKLISTED = "blocklisted"
REASON_NOT_ALLOWED = "not_allowed"
REASON_NOT_AUTHENTICATED = "sender_not_authenticated"

# Stable short codes put at the start of InboundEmailIngestionLog.error_message
# for a policy/DMARC refusal ("<code>: <human text>"), shown to the owner by
# GET /api/v1/apps/{id}/inbound-email-logs/. Do not rename: clients key on them.
LOG_CODE_SENDER_NOT_ALLOWED = "sender_not_allowed"
LOG_CODE_SENDER_BLOCKED = "sender_blocked"
LOG_CODE_DMARC_FAILED = "dmarc_failed"
_LOG_CODES = {
    REASON_NOT_ALLOWED: LOG_CODE_SENDER_NOT_ALLOWED,
    REASON_BLOCKLISTED: LOG_CODE_SENDER_BLOCKED,
    REASON_NOT_AUTHENTICATED: LOG_CODE_DMARC_FAILED,
}


def log_code_for_rejection(reason: str) -> str:
    """Map a REASON_* refusal to its stable journal code."""
    return _LOG_CODES.get(reason, reason)

# Acceptance reasons.
ACCEPTED_OWNER = "owner"
ACCEPTED_ALLOWLIST = "allowlisted"
ACCEPTED_ANYONE = "anyone"


def normalize_entry(raw: str) -> str:
    """Lowercase + validate one list entry. Raises ValidationError."""
    entry = str(raw or "").strip().lower()
    if not entry:
        raise ValidationError("Empty entry.")
    if len(entry) > MAX_SENDER_ENTRY_LENGTH:
        raise ValidationError(f"Entry is too long: {entry[:40]}...")
    if entry.startswith("@"):
        if entry.startswith(WILDCARD_DOMAIN_PREFIX):
            domain = entry[len(WILDCARD_DOMAIN_PREFIX):]
        else:
            domain = entry[1:]
        # "*" is only valid as the whole first label ("@*.example.com").
        if "*" in domain:
            raise ValidationError(f"Invalid domain entry (wildcard only as '@*.domain'): {entry}")
        # Reuse Django's address validator on a probe local part to validate
        # the domain (no "@" inside, has a dot, valid labels).
        if not domain or "@" in domain:
            raise ValidationError(f"Invalid domain entry: {entry}")
        try:
            validate_email(f"probe@{domain}")
        except ValidationError:
            raise ValidationError(f"Invalid domain entry: {entry}")
        return entry
    if "*" in entry:
        # validate_email accepts "*" in a local part; a wildcard address is
        # never what the owner means.
        raise ValidationError(f"Invalid e-mail address (wildcard only as '@*.domain'): {entry}")
    try:
        validate_email(entry)
    except ValidationError:
        raise ValidationError(f"Invalid e-mail address: {entry}")
    return entry


def normalize_entries(values) -> list[str]:
    """Validate, lowercase and dedupe a list of entries (order kept).

    Raises ValidationError with one message per invalid entry, or when the
    list exceeds MAX_SENDER_LIST_ENTRIES once deduplicated.
    """
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValidationError("Expected a list of addresses, @domains or @*.domains.")
    result: list[str] = []
    seen: set[str] = set()
    errors: list[str] = []
    for raw in values:
        if not str(raw or "").strip():
            continue  # blank lines from a textarea are not errors
        try:
            entry = normalize_entry(raw)
        except ValidationError as exc:
            errors.extend(exc.messages)
            continue
        if entry not in seen:
            seen.add(entry)
            result.append(entry)
    if errors:
        raise ValidationError(errors)
    if len(result) > MAX_SENDER_LIST_ENTRIES:
        raise ValidationError(f"At most {MAX_SENDER_LIST_ENTRIES} entries are allowed.")
    return result


def sender_matches(sender: str, entries) -> bool:
    """True when ``sender`` matches an entry (case-insensitive):

    - ``alice@example.com``: that exact address;
    - ``@example.com``: that exact domain (no subdomain);
    - ``@*.example.com``: that domain or any subdomain of it, on a label
      boundary (``badexample.com`` does not match).
    """
    sender = (sender or "").strip().lower()
    if "@" not in sender:
        return False
    sender_domain = sender.rpartition("@")[2]
    if not sender_domain:
        return False
    for entry in entries or ():
        entry = str(entry).strip().lower()
        if entry.startswith(WILDCARD_DOMAIN_PREFIX):
            base = entry[len(WILDCARD_DOMAIN_PREFIX):]
            if base and (sender_domain == base or sender_domain.endswith("." + base)):
                return True
        elif entry == sender or entry == "@" + sender_domain:
            return True
    return False


@dataclass(frozen=True)
class SenderPolicyDecision:
    accepted: bool
    reason: str  # ACCEPTED_* when accepted, REASON_* when refused
    is_owner: bool


def evaluate_sender_policy(application, sender: str) -> SenderPolicyDecision:
    sender = (sender or "").strip().lower()
    owner_email = (getattr(application.owner, "email", "") or "").strip().lower()
    is_owner = bool(owner_email) and sender == owner_email

    if is_owner:
        # The blocklist never blocks the owner's own address.
        return SenderPolicyDecision(True, ACCEPTED_OWNER, True)
    if sender_matches(sender, application.inbound_sender_blocklist):
        return SenderPolicyDecision(False, REASON_BLOCKLISTED, False)
    if application.inbound_sender_mode == MODE_ANYONE:
        return SenderPolicyDecision(True, ACCEPTED_ANYONE, False)
    if sender_matches(sender, application.inbound_sender_allowlist):
        return SenderPolicyDecision(True, ACCEPTED_ALLOWLIST, False)
    return SenderPolicyDecision(False, REASON_NOT_ALLOWED, False)
