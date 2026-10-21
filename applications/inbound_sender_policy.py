"""Per-application policy: who may create notifications by e-mail.

An application's inbound alias accepts mail according to:

- ``inbound_sender_mode`` = ``owner`` (default): the owner's own address, plus
  any sender matching ``inbound_sender_allowlist``;
- ``inbound_sender_mode`` = ``anyone``: every sender.

``inbound_sender_blocklist`` applies in both modes and wins over the allowlist,
but never blocks the owner's own address (the owner cannot lock themselves out
by listing their own domain).

List entries are either an exact address (``alice@example.com``) or a whole
domain (``@example.com`` -- exact domain match, subdomains are NOT included).
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

# Refusal reasons (journalled, shown to the owner in the notice).
REASON_BLOCKLISTED = "blocklisted"
REASON_NOT_ALLOWED = "not_allowed"
REASON_NOT_AUTHENTICATED = "sender_not_authenticated"

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
        domain = entry[1:]
        # Reuse Django's address validator on a probe local part to validate
        # the domain (no "@" inside, has a dot, valid labels).
        if not domain or "@" in domain:
            raise ValidationError(f"Invalid domain entry: {entry}")
        try:
            validate_email(f"probe@{domain}")
        except ValidationError:
            raise ValidationError(f"Invalid domain entry: {entry}")
        return entry
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
        raise ValidationError("Expected a list of addresses or @domains.")
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
    """True when ``sender`` equals an address entry or its domain equals a
    ``@domain`` entry (case-insensitive, exact domain)."""
    sender = (sender or "").strip().lower()
    if "@" not in sender:
        return False
    domain = "@" + sender.rpartition("@")[2]
    for entry in entries or ():
        entry = str(entry).strip().lower()
        if entry == sender or entry == domain:
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
