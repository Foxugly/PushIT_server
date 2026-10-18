"""Sender authentication for inbound e-mail (audit S-I4).

The `From` of an inbound mail decides which user is creating the notification,
and that header is forgeable at the SMTP layer. Mail reaches us only by polling
an Exchange Online mailbox, so we read the verdicts Exchange stamped on receipt:

* ``Authentication-Results`` — SPF/DKIM/DMARC for mail that crossed the SMTP
  boundary. Exchange prepends its own line, and the poller keeps the first
  occurrence, so a header injected by the sender cannot shadow it.
* ``X-MS-Exchange-Organization-AuthAs`` — ``Internal`` when the mail was
  submitted by an authenticated user of our own tenant (such mail never crosses
  the boundary and often carries no Authentication-Results at all). Exchange's
  header firewall strips every ``X-MS-Exchange-Organization-*`` header from mail
  arriving from outside, so an external sender cannot forge it.

Verdicts (M365 ``dmarc=`` values):

* ``pass``          aligned SPF or DKIM, domain publishes DMARC → accepted
* ``bestguesspass`` no DMARC record, but SPF/DKIM aligned → accepted unless
                    ``INBOUND_EMAIL_DMARC_ACCEPT_BESTGUESSPASS`` is False
* ``none``          no DMARC record and no aligned pass: the sender is NOT
                    authenticated → refused unless
                    ``INBOUND_EMAIL_DMARC_ALLOW_NO_POLICY`` is True
* ``fail`` / ``temperror`` / ``permerror`` / anything else → refused
* no header, or a verdict for another ``header.from`` domain → refused
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

AUTH_AS_HEADER = "x-ms-exchange-organization-authas"

_DMARC_RE = re.compile(r"\bdmarc=([a-z]+)")
_HEADER_FROM_RE = re.compile(r"\bheader\.from=([^\s;]+)")


@dataclass(frozen=True)
class SenderAuthVerdict:
    accepted: bool
    # Why: dmarc_pass, dmarc_bestguesspass, internal_org, dmarc_none, dmarc_fail,
    # dmarc_<other>, misaligned, no_auth_results, no_dmarc_verdict.
    reason: str
    # Raw M365 dmarc= token, or "" when absent.
    dmarc_verdict: str = ""


def evaluate_sender_authentication(
    authentication_results: str, sender_domain: str, auth_as: str = ""
) -> SenderAuthVerdict:
    if (
        getattr(settings, "INBOUND_EMAIL_DMARC_TRUST_INTERNAL", True)
        and (auth_as or "").strip().lower() == "internal"
    ):
        return SenderAuthVerdict(True, "internal_org")

    lowered = (authentication_results or "").lower()
    if not lowered.strip():
        return SenderAuthVerdict(False, "no_auth_results")

    match = _DMARC_RE.search(lowered)
    if match is None:
        return SenderAuthVerdict(False, "no_dmarc_verdict")
    verdict = match.group(1)

    # A verdict evaluated for another From domain says nothing about ours.
    header_from = _HEADER_FROM_RE.search(lowered)
    if header_from is not None and header_from.group(1).rstrip(".") != (sender_domain or "").lower():
        return SenderAuthVerdict(False, "misaligned", verdict)

    if verdict == "pass":
        return SenderAuthVerdict(True, "dmarc_pass", verdict)
    if verdict == "bestguesspass":
        accepted = bool(getattr(settings, "INBOUND_EMAIL_DMARC_ACCEPT_BESTGUESSPASS", True))
        return SenderAuthVerdict(accepted, "dmarc_bestguesspass", verdict)
    if verdict == "none":
        accepted = bool(getattr(settings, "INBOUND_EMAIL_DMARC_ALLOW_NO_POLICY", False))
        return SenderAuthVerdict(accepted, "dmarc_none", verdict)
    return SenderAuthVerdict(False, f"dmarc_{verdict}", verdict)
