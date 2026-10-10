"""Inbound-email alias format: generation and recognition.

Current format: ``<name-slug>-<8 hex><suffix>`` (suffix =
``settings.INBOUND_EMAIL_ALIAS_SUFFIX``, ``.pushit`` by default), e.g.
``mon-app-3f9a2c1b.pushit@foxugly.com``. The random hex keeps the address
non-guessable; the suffix marks it as a PushIT ingestion alias.

Legacy format, still accepted: ``app_<slug>_<8 hex>``.

Everything here is case-insensitive: mail systems do not preserve the case of
the local part reliably.
"""

from __future__ import annotations

import re
import secrets
from email.utils import parseaddr

from django.conf import settings
from django.utils.text import slugify

LEGACY_ALIAS_PREFIX = "app_"
ALIAS_RANDOM_BYTES = 4  # -> 8 hex chars
# RFC 5321 caps the local part at 64 octets.
MAX_LOCAL_PART_LENGTH = 64
DEFAULT_SLUG = "app"


def alias_suffix() -> str:
    return (getattr(settings, "INBOUND_EMAIL_ALIAS_SUFFIX", ".pushit") or "").strip().lower()


def inbound_domain() -> str:
    return settings.INBOUND_EMAIL_DOMAIN.strip().lower()


def mailbox_addresses() -> set[str]:
    """The polled mailbox's own address(es) — never an application alias."""
    candidates = (
        getattr(settings, "GRAPH_MAILBOX_USER_ID", ""),
        getattr(settings, "EXCHANGE_SHARED_MAILBOX", ""),
    )
    return {c.strip().lower() for c in candidates if c and "@" in c}


def normalize_address(value: str) -> str:
    """``"Name <A@B.com>"`` / ``"a@b.com"`` -> ``"a@b.com"`` (lowercased)."""
    return parseaddr(value or "")[1].strip().lower()


def generate_alias(name: str) -> str:
    suffix = alias_suffix()
    random_part = secrets.token_hex(ALIAS_RANDOM_BYTES)
    slug = re.sub(r"-+", "-", slugify(name)).strip("-") or DEFAULT_SLUG
    # Keep the local part within 64 chars: slug + "-" + hex + suffix.
    room = MAX_LOCAL_PART_LENGTH - 1 - len(random_part) - len(suffix)
    slug = slug[: max(room, 1)].strip("-") or DEFAULT_SLUG[: max(room, 1)]
    return f"{slug}-{random_part}{suffix}"


def random_part_of(alias: str) -> str:
    """The random, DB-unique part of an alias (both formats)."""
    alias = (alias or "").lower()
    suffix = alias_suffix()
    if suffix and alias.endswith(suffix):
        return alias[: -len(suffix)].rsplit("-", 1)[-1]
    # Legacy app_<slug>_<hex> (and the older free-form aliases).
    return alias.rsplit("_", 1)[-1]


def is_current_format(local_part: str) -> bool:
    local_part = (local_part or "").lower()
    suffix = alias_suffix()
    return bool(suffix) and local_part.endswith(suffix) and len(local_part) > len(suffix)


def is_legacy_format(local_part: str) -> bool:
    local_part = (local_part or "").lower()
    return local_part.startswith(LEGACY_ALIAS_PREFIX) and len(local_part) > len(LEGACY_ALIAS_PREFIX)


def is_alias_local_part(local_part: str) -> bool:
    return is_current_format(local_part) or is_legacy_format(local_part)


def is_inbound_alias_address(address: str) -> bool:
    """True when ``address`` looks like an application alias on the inbound
    domain: right domain, alias-shaped local part, and not the mailbox itself."""
    address = normalize_address(address)
    if not address or "@" not in address or address in mailbox_addresses():
        return False
    local_part, domain = address.rsplit("@", 1)
    return domain == inbound_domain() and is_alias_local_part(local_part)
