"""Client du service de facturation centralisé (billing-api.foxugly.com).

PushIT ne détient aucune clé Stripe : il délègue au central, qui seul parle à
Stripe. Les échanges sont signés en HMAC-SHA256 dans les deux sens.

Ce module est le SEUL point de sortie vers le central. Toute panne y est traduite
en une erreur explicite : le SPA doit voir un 503 clair, jamais une exception.
"""
import hashlib
import hmac
import json
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger("pushit")

TIMEOUT_SECONDS = 10


class BillingUnavailable(Exception):
    """Le central est injoignable ou répond de travers."""


def _sign(method: str, path: str, body: bytes, timestamp: int) -> str:
    """La signature couvre l'horodatage, la METHODE et le CHEMIN COMPLET.

    Sans la methode et le chemin, deux GET a corps vide emis dans la meme seconde
    produisent une signature identique : l'anti-rejeu du central rejette alors le
    second, pourtant legitime. Et une signature capturee pour une route en
    ouvrirait une autre. Constate le 2026-07-28.
    """
    payload = f"{timestamp}.{method.upper()}.{path}.".encode() + (body or b"")
    digest = hmac.new(settings.BILLING_APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _headers(method: str, path: str, body: bytes) -> dict:
    timestamp = int(time.time())
    return {
        "Content-Type": "application/json",
        "X-Foxugly-App": settings.BILLING_APP_SLUG,
        "X-Foxugly-Timestamp": str(timestamp),
        "X-Foxugly-Signature": _sign(method, path, body, timestamp),
    }


def _url(path: str) -> str:
    return f"{settings.BILLING_BASE_URL.rstrip('/')}{_path(path)}"


def _path(path: str) -> str:
    """Le chemin signe, tel que le central le reconstruira (query comprise)."""
    return f"/api/v1/{path.lstrip('/')}"


def post(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    try:
        response = requests.post(
            _url(path), data=body, headers=_headers("POST", _path(path), body), timeout=TIMEOUT_SECONDS
        )
    except Exception as exc:
        logger.warning("billing_unreachable", extra={"path": path, "error": str(exc)})
        raise BillingUnavailable(str(exc)) from exc

    if response.status_code >= 400:
        logger.warning("billing_error", extra={"path": path, "code": response.status_code})
        raise BillingUnavailable(f"HTTP {response.status_code}")
    return response.json()


def get(path: str) -> dict:
    # Un GET signe le corps vide : l'horodatage suffit à borner la signature.
    try:
        response = requests.get(
            _url(path), headers=_headers("GET", _path(path), b""), timeout=TIMEOUT_SECONDS
        )
    except Exception as exc:
        logger.warning("billing_unreachable", extra={"path": path, "error": str(exc)})
        raise BillingUnavailable(str(exc)) from exc

    if response.status_code >= 400:
        logger.warning("billing_error", extra={"path": path, "code": response.status_code})
        raise BillingUnavailable(f"HTTP {response.status_code}")
    return response.json()


def verify_inbound(method: str, path: str, body: bytes, timestamp, signature: str) -> bool:
    """Vérifie une requête entrante signée par le central (push de droit).

    Même algorithme, même secret, fenêtre de 5 minutes. L'anti-rejeu, lui, repose
    sur le `delivery_id` porté dans le corps : c'est la clé d'idempotence du
    central, et elle est plus fiable ici qu'un cache local.
    """
    if not signature or not signature.startswith("sha256="):
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > 300:
        return False
    return hmac.compare_digest(_sign(method, path, body, ts), signature)
