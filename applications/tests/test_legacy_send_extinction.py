"""L'extinction : le jeton historique n'émet plus.

Dernier geste du plan. Il ferme l'entrée que les tâches 1 à 5 avaient rendue
inutile : tant que l'ancien jeton pouvait émettre, quiconque l'avait reçu par un
vieux QR gardait la capacité d'écrire à tous les terminaux de l'application.

Ce qu'il ne ferme PAS, et ces tests le gardent : l'enrôlement. Des téléphones
ont stocké l'ancien jeton et s'en servent encore pour se rattacher ; le refuser
là les couperait sans que personne y gagne quoi que ce soit.
"""
import itertools
from unittest.mock import patch

import pytest
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
def destinataire(db):
    return User.objects.create_user(email="destinataire@example.com", password=MOT_DE_PASSE)


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


# ------------------------------------------------------- ce qui est éteint

@pytest.mark.django_db
def test_the_legacy_token_can_no_longer_send(app, legacy_token):
    """Le geste de l'extinction. C'était la derniere porte par laquelle un
    destinataire pouvait ecrire aux autres."""
    r = _envoyer(legacy_token)

    assert r.status_code == 401


@pytest.mark.django_db
def test_the_refusal_says_what_to_do(app, legacy_token):
    """Un 401 muet enverrait chercher un probleme d'authentification. Le code
    d'erreur doit dire que le jeton est retire, pas invalide."""
    r = _envoyer(legacy_token)

    assert r.json()["code"] == "app_token_retired"


@pytest.mark.django_db
def test_the_legacy_token_can_no_longer_read_the_notifications(app, legacy_token):
    """Lire les envois d'une application est aussi une fuite : l'extinction porte
    sur toutes les routes d'application, pas seulement l'emission."""
    r = APIClient().get("/api/v1/notifications/app/", HTTP_X_APP_TOKEN=legacy_token)

    assert r.status_code == 401


@pytest.mark.django_db
def test_an_extinct_send_is_never_recorded_as_a_legacy_use(app, legacy_token):
    """Le drapeau servait a savoir quand couper. Une fois coupe, il ne doit plus
    bouger -- sinon la console rallumerait son bandeau pour un envoi refuse."""
    _envoyer(legacy_token)

    app.refresh_from_db()
    assert app.legacy_send_last_used_at is None


# ------------------------------------------------- ce qui continue de vivre

@pytest.mark.django_db
def test_the_legacy_token_still_links_a_device(app, destinataire, legacy_token):
    """L'invariant a ne pas casser : des installations mobiles portent encore ce
    jeton et s'en servent pour se rattacher."""
    r = _link(_as(destinataire), legacy_token)

    assert r.status_code == 200


@pytest.mark.django_db
def test_the_enrolment_code_still_links_a_device(app, destinataire):
    r = _link(_as(destinataire), app.enrolment_code)

    assert r.status_code == 200


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_a_send_token_still_sends(app):
    """L'extinction ne doit pas emporter le chemin qui la remplace."""
    _, brut = AppSendToken.issue(app, "prod")

    assert _envoyer(brut).status_code in (200, 201, 202)


@pytest.mark.django_db
def test_the_enrolment_code_still_cannot_send(app):
    """L'invariant central du plan, verifie une derniere fois apres extinction."""
    r = _envoyer(app.enrolment_code)

    assert r.status_code == 401
