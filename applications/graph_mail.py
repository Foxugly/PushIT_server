import logging

import requests
from django.conf import settings
from msal import ConfidentialClientApplication

logger = logging.getLogger(__name__)

_msal_app = None


def _get_msal_app():
    global _msal_app
    if _msal_app is None:
        _msal_app = ConfidentialClientApplication(
            settings.GRAPH_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{settings.GRAPH_TENANT_ID}",
            client_credential=settings.GRAPH_CLIENT_SECRET,
        )
    return _msal_app


def _is_configured() -> bool:
    return bool(getattr(settings, "GRAPH_CLIENT_ID", ""))


def _get_access_token() -> str:
    app = _get_msal_app()
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Failed to acquire Graph API token: {result.get('error_description', result)}")
    return result["access_token"]


def _build_smtp_alias(alias: str) -> str:
    domain = settings.INBOUND_EMAIL_DOMAIN.strip().lower()
    return f"smtp:{alias}@{domain}"


def add_email_alias(alias: str) -> None:
    if not _is_configured():
        logger.warning("graph_mail_skipped", extra={"reason": "not configured", "alias": alias})
        return

    try:
        access_token = _get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        user_id = settings.GRAPH_MAILBOX_USER_ID

        r = requests.get(
            f"https://graph.microsoft.com/v1.0/users/{user_id}?$select=proxyAddresses",
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()

        proxy_addresses = r.json().get("proxyAddresses", [])
        smtp_alias = _build_smtp_alias(alias)

        if smtp_alias.lower() not in [a.lower() for a in proxy_addresses]:
            proxy_addresses.append(smtp_alias)
            r = requests.patch(
                f"https://graph.microsoft.com/v1.0/users/{user_id}",
                headers=headers,
                json={"proxyAddresses": proxy_addresses},
                timeout=30,
            )
            r.raise_for_status()
            logger.info("graph_mail_alias_added", extra={"alias": alias, "user_id": user_id})
        else:
            logger.info("graph_mail_alias_already_exists", extra={"alias": alias, "user_id": user_id})

    except Exception:
        logger.exception("graph_mail_add_alias_failed", extra={"alias": alias})


def remove_email_alias(alias: str) -> None:
    if not _is_configured():
        logger.warning("graph_mail_skipped", extra={"reason": "not configured", "alias": alias})
        return

    try:
        access_token = _get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        user_id = settings.GRAPH_MAILBOX_USER_ID

        r = requests.get(
            f"https://graph.microsoft.com/v1.0/users/{user_id}?$select=proxyAddresses",
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()

        proxy_addresses = r.json().get("proxyAddresses", [])
        smtp_alias = _build_smtp_alias(alias)
        filtered = [a for a in proxy_addresses if a.lower() != smtp_alias.lower()]

        if len(filtered) < len(proxy_addresses):
            r = requests.patch(
                f"https://graph.microsoft.com/v1.0/users/{user_id}",
                headers=headers,
                json={"proxyAddresses": filtered},
                timeout=30,
            )
            r.raise_for_status()
            logger.info("graph_mail_alias_removed", extra={"alias": alias, "user_id": user_id})
        else:
            logger.info("graph_mail_alias_not_found", extra={"alias": alias, "user_id": user_id})

    except Exception:
        logger.exception("graph_mail_remove_alias_failed", extra={"alias": alias})
