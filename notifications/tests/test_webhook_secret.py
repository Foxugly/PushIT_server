"""Le secret qui signe les webhooks doit être à l'application, et à elle seule.

Il était jusqu'ici l'empreinte du jeton applicatif historique — c'est-à-dire le
SHA-256 d'une valeur distribuée dans le QR à **chaque destinataire**. Qui avait
scanné ce QR pouvait donc recalculer l'empreinte et forger un callback signé
vers l'endpoint du propriétaire.

Second défaut, plus certain : supprimer les colonnes `app_token_*` (étape
restante de l'extinction) aurait cassé la signature en silence.
"""
import hashlib
import hmac
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application

MOT_DE_PASSE = "1234Test!!"


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="proprio@example.com", password=MOT_DE_PASSE)


@pytest.fixture
def app(owner):
    application = Application(owner=owner, name="App", webhook_url="https://exemple.test/hook")
    application.set_new_app_token()
    application.save()
    return application


def _as(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def _envoyer_callback(app):
    from notifications.webhooks import send_webhook_callback_task

    with patch("notifications.webhooks.requests.post") as poste:
        poste.return_value.status_code = 200
        send_webhook_callback_task(
            application_id=app.id, notification_id=1, final_status="sent", sent_at=None
        )
        return poste.call_args


# ------------------------------------------------------------- le secret existe

@pytest.mark.django_db
def test_every_application_gets_its_own_webhook_secret(owner):
    une = Application(owner=owner, name="Une")
    une.set_new_app_token()
    une.save()
    autre = Application(owner=owner, name="Autre")
    autre.set_new_app_token()
    autre.save()

    assert une.webhook_secret
    assert une.webhook_secret != autre.webhook_secret


@pytest.mark.django_db
def test_the_secret_is_not_derived_from_the_legacy_token(app):
    """Le defaut corrige : l'ancien secret etait le SHA-256 du jeton distribue
    dans le QR. N'importe quel destinataire pouvait le recalculer."""
    assert app.webhook_secret != app.app_token_hash
    assert app.webhook_secret != hashlib.sha256(app.app_token_hash.encode()).hexdigest()


# --------------------------------------------------------- il signe vraiment

@pytest.mark.django_db
def test_the_callback_is_signed_with_the_webhook_secret(app):
    # Patché là où la fonction est utilisée : `webhooks` l'importe par nom, donc
    # patcher son module d'origine n'atteindrait pas la référence locale.
    with patch("notifications.webhooks.assert_webhook_url_safe"):
        appel = _envoyer_callback(app)

    corps = appel.kwargs["data"]
    attendu = hmac.new(app.webhook_secret.encode(), corps, hashlib.sha256).hexdigest()
    assert appel.kwargs["headers"]["X-PushIT-Signature"] == attendu


@pytest.mark.django_db
def test_the_callback_is_no_longer_signed_with_the_legacy_hash(app):
    """Le test qui aurait du exister avant : si la signature retombait sur
    l'empreinte heritee, la faille reviendrait sans bruit."""
    # Patché là où la fonction est utilisée : `webhooks` l'importe par nom, donc
    # patcher son module d'origine n'atteindrait pas la référence locale.
    with patch("notifications.webhooks.assert_webhook_url_safe"):
        appel = _envoyer_callback(app)

    corps = appel.kwargs["data"]
    ancienne = hmac.new(app.app_token_hash.encode(), corps, hashlib.sha256).hexdigest()
    assert appel.kwargs["headers"]["X-PushIT-Signature"] != ancienne


# ------------------------------------------------------------- il ne fuit pas

@pytest.mark.django_db
def test_the_secret_never_appears_in_the_application_payload(app, owner):
    """La console garde les applications en cache ; un secret dans cette charge
    utile finirait dans le stockage du navigateur de chaque onglet ouvert."""
    detail = _as(owner).get(f"/api/v1/apps/{app.id}/").json()
    liste = _as(owner).get("/api/v1/apps/").json()

    assert "webhook_secret" not in detail
    assert app.webhook_secret not in str(liste)


# ------------------------------------------------------------- il se relit

@pytest.mark.django_db
def test_revealing_requires_the_password(app, owner):
    r = _as(owner).post(
        f"/api/v1/apps/{app.id}/webhook-secret/reveal/", {"password": "mauvais"}, format="json"
    )

    assert r.status_code == 403


@pytest.mark.django_db
def test_revealing_returns_the_signing_secret(app, owner):
    """Le proprietaire doit pouvoir le lire : sans lui, il ne peut pas verifier
    la signature de son cote."""
    r = _as(owner).post(
        f"/api/v1/apps/{app.id}/webhook-secret/reveal/", {"password": MOT_DE_PASSE}, format="json"
    )

    assert r.status_code == 200
    assert r.json()["webhook_secret"] == app.webhook_secret


@pytest.mark.django_db
def test_a_stranger_reveals_nothing(app, db):
    autre = User.objects.create_user(email="autre@example.com", password=MOT_DE_PASSE)

    r = _as(autre).post(
        f"/api/v1/apps/{app.id}/webhook-secret/reveal/", {"password": MOT_DE_PASSE}, format="json"
    )

    assert r.status_code == 404


# --------------------------------------------------------------- il tourne

@pytest.mark.django_db
def test_rotating_changes_the_secret_and_returns_it_once(app, owner):
    ancien = app.webhook_secret

    r = _as(owner).post(f"/api/v1/apps/{app.id}/webhook-secret/rotate/", {}, format="json")

    assert r.status_code == 200
    assert r.json()["webhook_secret"] != ancien
    app.refresh_from_db()
    assert app.webhook_secret == r.json()["webhook_secret"]


@pytest.mark.django_db
def test_a_stranger_cannot_rotate(app, db):
    autre = User.objects.create_user(email="autre@example.com", password=MOT_DE_PASSE)
    ancien = app.webhook_secret

    r = _as(autre).post(f"/api/v1/apps/{app.id}/webhook-secret/rotate/", {}, format="json")

    assert r.status_code == 404
    app.refresh_from_db()
    assert app.webhook_secret == ancien
