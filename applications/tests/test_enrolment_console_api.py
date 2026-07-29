"""Ce que la console doit pouvoir montrer et faire.

Le code d'enrôlement existe (tâche 1) et les jetons d'émission aussi (tâche 2) ;
il manquait de quoi les piloter depuis la console : lire le code, le faire
tourner, en tirer un QR, et savoir si l'application émet encore avec le jeton
hérité — ce dernier point conditionne l'extinction, qui casserait sinon
l'intégration de quelqu'un sans prévenir.
"""
import itertools
from unittest.mock import patch

import pytest
import qrcode
from cryptography.fernet import Fernet
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from applications.models_send_token import AppSendToken

MOT_DE_PASSE = "1234Test!!"
CHIFFREMENT = {"APP_TOKEN_ENCRYPTION_KEYS": [Fernet.generate_key().decode()]}


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="proprio@example.com", password=MOT_DE_PASSE)


@pytest.fixture
def autre(db):
    return User.objects.create_user(email="autre@example.com", password=MOT_DE_PASSE)


@pytest.fixture
def app(owner):
    application = Application(owner=owner, name="App")
    application.set_new_app_token()
    application.save()
    return application


@pytest.fixture
def legacy_token(app):
    """Le jeton historique de cette application, en clair."""
    brut = app.set_new_app_token()
    app.save(update_fields=["app_token_prefix", "app_token_hash", "revoked_at", "last_used_at"])
    return brut


def _as(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


_compteur = itertools.count()


def _envoyer(jeton_brut):
    with patch("notifications.api_views.send_notification_task.delay") as depeche:
        depeche.return_value.id = "task-test"
        return APIClient().post(
            "/api/v1/notifications/app/send/",
            {"title": "x", "message": "y"},
            format="json",
            HTTP_X_APP_TOKEN=jeton_brut,
            HTTP_IDEMPOTENCY_KEY=f"envoi-{next(_compteur)}",
        )


def _link(client, code, push_token="token_12345678901234567890"):
    return client.post(
        "/api/v1/devices/link/",
        {
            "app_token": code,
            "device_name": "Pixel",
            "platform": "android",
            "push_token": push_token,
        },
        format="json",
    )


# ------------------------------------------------------------- lire le code

@pytest.mark.django_db
def test_the_owner_reads_the_enrolment_code(app, owner):
    """Il doit s'afficher en permanence : c'est ce qu'on distribue. Le montrer
    derrière une révélation ferait croire à un secret."""
    r = _as(owner).get(f"/api/v1/apps/{app.id}/")

    assert r.status_code == 200
    assert r.json()["enrolment_code"] == app.enrolment_code


@pytest.mark.django_db
def test_the_list_carries_the_code_too(app, owner):
    """La console garde les applications en cache depuis la liste : sans le code
    ici, la page détail l'afficherait vide le temps d'un aller-retour."""
    r = _as(owner).get("/api/v1/apps/")

    assert r.json()[0]["enrolment_code"] == app.enrolment_code


@pytest.mark.django_db
def test_a_stranger_reads_nothing(app, autre):
    assert _as(autre).get(f"/api/v1/apps/{app.id}/").status_code == 404


# ---------------------------------------------------------- faire tourner

@pytest.mark.django_db
def test_rotating_from_the_console_changes_the_code(app, owner):
    ancien = app.enrolment_code

    r = _as(owner).post(f"/api/v1/apps/{app.id}/rotate-enrolment-code/", {}, format="json")

    assert r.status_code == 200
    assert r.json()["enrolment_code"] != ancien
    app.refresh_from_db()
    assert app.enrolment_code == r.json()["enrolment_code"]


@pytest.mark.django_db
def test_rotating_closes_the_door_to_newcomers(app, owner, autre):
    ancien = app.enrolment_code

    _as(owner).post(f"/api/v1/apps/{app.id}/rotate-enrolment-code/", {}, format="json")

    assert _link(_as(autre), ancien).status_code == 401


@pytest.mark.django_db
def test_rotating_evicts_nobody(app, owner, autre):
    """Le point que la console doit dire noir sur blanc : faire tourner le code
    ferme la porte aux futurs, il ne retire personne. Pour retirer quelqu'un, il
    y a la liste des terminaux."""
    _link(_as(autre), app.enrolment_code)

    _as(owner).post(f"/api/v1/apps/{app.id}/rotate-enrolment-code/", {}, format="json")

    assert app.device_links.filter(is_active=True).count() == 1


@pytest.mark.django_db
def test_a_stranger_cannot_rotate(app, autre):
    ancien = app.enrolment_code

    r = _as(autre).post(f"/api/v1/apps/{app.id}/rotate-enrolment-code/", {}, format="json")

    assert r.status_code == 404
    app.refresh_from_db()
    assert app.enrolment_code == ancien


# ------------------------------------------------------------------- le QR

@pytest.mark.django_db
def test_the_qr_encodes_the_enrolment_code(app, owner, monkeypatch):
    """Le QR est ce qui circule : il doit porter le code, jamais un jeton
    d'émission. Se tromper ici redistribuerait la capacité d'écrire."""
    encode = []

    class Espion(qrcode.QRCode):
        def add_data(self, data, optimize=20):
            encode.append(data)
            return super().add_data(data, optimize)

    monkeypatch.setattr(qrcode, "QRCode", Espion)

    r = _as(owner).get(f"/api/v1/apps/{app.id}/qrcode/")

    assert r.status_code == 200
    assert r["Content-Type"] == "image/png"
    assert encode == [app.enrolment_code]


@pytest.mark.django_db
def test_the_qr_needs_no_secret_from_the_caller(app, owner):
    """L'ancien QR exigeait le jeton brut en clair dans la requête — donc il
    n'était plus atteignable une fois la page rechargée. Le code, lui, est
    stocké en clair : la console le redemande quand elle veut."""
    r = _as(owner).get(f"/api/v1/apps/{app.id}/qrcode/")

    assert r.status_code == 200
    assert len(r.content) > 100


@pytest.mark.django_db
def test_a_stranger_gets_no_qr(app, autre):
    assert _as(autre).get(f"/api/v1/apps/{app.id}/qrcode/").status_code == 404


# ------------------------------------------- savoir si l'ancien jeton sert

@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_sending_with_the_legacy_token_is_recorded_on_the_application(app, owner, legacy_token):
    """C'est la condition d'extinction : sans ce drapeau, couper le jeton hérité
    casserait l'intégration de quelqu'un sans prévenir."""
    assert app.legacy_send_last_used_at is None

    _envoyer(legacy_token)

    app.refresh_from_db()
    assert app.legacy_send_last_used_at is not None


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_sending_with_a_send_token_raises_no_warning(app, owner):
    """Sinon le bandeau resterait allumé chez tout le monde et ne dirait plus
    rien."""
    _, brut = AppSendToken.issue(app, "prod")

    _envoyer(brut)

    app.refresh_from_db()
    assert app.legacy_send_last_used_at is None


@pytest.mark.django_db
def test_linking_with_the_legacy_token_is_not_a_send(app, autre, legacy_token):
    """L'installation mobile publiée enrôle encore avec l'ancien jeton, et c'est
    prévu : l'extinction ne porte que sur l'émission. Confondre les deux
    allumerait le bandeau chez toutes les applications à chaque scan de QR."""
    _link(_as(autre), legacy_token)

    app.refresh_from_db()
    assert app.legacy_send_last_used_at is None


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_the_console_can_read_the_warning(app, owner, legacy_token):
    _envoyer(legacy_token)

    r = _as(owner).get(f"/api/v1/apps/{app.id}/")

    assert r.json()["legacy_send_last_used_at"] is not None
