"""Jetons d'émission : multiples, révocables, relisibles sous condition.

Ils reprennent le métier « envoyer » du jeton historique, et **seulement**
celui-là. Le code d'enrôlement, lui, part vers chaque destinataire — ces tests
vérifient surtout que les deux ne se croisent jamais.
"""
import itertools
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.crypto import RevealUnavailable
from applications.models import Application
from applications.models_send_token import (
    MAX_TOKENS_PER_APPLICATION,
    AppSendToken,
    SendTokenReveal,
)

MOT_DE_PASSE = "1234Test!!"
CLE = Fernet.generate_key().decode()
CHIFFREMENT = {"APP_TOKEN_ENCRYPTION_KEYS": [CLE]}


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="proprio@example.com", password=MOT_DE_PASSE)


@pytest.fixture
def app(owner):
    application = Application(owner=owner, name="App")
    application.set_new_app_token()
    application.save()
    return application


def _as(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


_compteur = itertools.count()


def _envoyer(jeton_brut):
    """L'endpoint exige une cle d'idempotence, et depeche une tache Celery.

    On neutralise la tache : ces tests portent sur l'authentification, pas sur
    la distribution. Une cle differente a chaque appel, sinon le second envoi
    serait dedoublonne et on testerait l'idempotence sans le vouloir.
    """
    with patch("notifications.api_views.send_notification_task.delay") as depeche:
        depeche.return_value.id = "task-test"
        return APIClient().post(
            "/api/v1/notifications/app/send/",
            {"title": "x", "message": "y"},
            format="json",
            HTTP_X_APP_TOKEN=jeton_brut,
            HTTP_IDEMPOTENCY_KEY=f"envoi-{next(_compteur)}",
        )


# ------------------------------------------------------------------ le format

@pytest.mark.django_db
def test_a_send_token_is_half_the_length_of_the_legacy_one(app):
    _, brut = AppSendToken.issue(app, "prod")

    assert brut.startswith("apt_")
    assert len(brut) == 26
    assert len(brut) < len(Application.generate_raw_app_token())


@pytest.mark.django_db
def test_the_stored_prefix_never_exposes_half_the_secret(app):
    """Raccourcir le jeton oblige a raccourcir le prefixe : 12 caracteres sur 26
    en montreraient presque la moitie."""
    jeton, brut = AppSendToken.issue(app, "prod")

    assert len(jeton.prefix) <= len(brut) // 2


# ------------------------------------------------------------------ il émet

@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_a_send_token_authenticates_an_emission(app):
    _, brut = AppSendToken.issue(app, "prod")

    assert _envoyer(brut).status_code in (200, 201, 202)


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_a_revoked_token_is_refused_immediately(app):
    """C'est le geste d'urgence : il ne doit pas attendre une expiration."""
    jeton, brut = AppSendToken.issue(app, "prod")

    jeton.revoke()

    assert _envoyer(brut).status_code == 401


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_revoking_one_token_leaves_the_others_alone(app):
    """Toute la raison d'etre des jetons multiples : remplacer sans interrompre."""
    ancien, brut_ancien = AppSendToken.issue(app, "ancien")
    _, brut_nouveau = AppSendToken.issue(app, "nouveau")

    ancien.revoke()

    assert _envoyer(brut_ancien).status_code == 401
    assert _envoyer(brut_nouveau).status_code in (200, 201, 202)


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_using_a_token_stamps_its_last_use(app):
    """C'est ce qui permet de savoir lequel sert encore avant de le supprimer."""
    jeton, brut = AppSendToken.issue(app, "prod")
    assert jeton.last_used_at is None

    _envoyer(brut)

    jeton.refresh_from_db()
    assert jeton.last_used_at is not None


# ------------------------------------------------- il ne fait QUE ça

@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_a_send_token_can_never_link_a_device(app, owner):
    """L'invariant miroir de celui du code d'enrolement. S'il pouvait enroler, on
    le distribuerait aux telephones -- et on recreerait exactement le defaut
    qu'on corrige."""
    _, brut = AppSendToken.issue(app, "prod")

    r = _as(owner).post(
        "/api/v1/devices/link/",
        {
            "app_token": brut,
            "device_name": "Pixel",
            "platform": "android",
            "push_token": "token_12345678901234567890",
        },
        format="json",
    )

    assert r.status_code == 401


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_the_enrolment_code_still_cannot_send(app):
    assert _envoyer(app.enrolment_code).status_code == 401


# ------------------------------------------------------------- la révélation

@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_revealing_returns_the_original_value(app, owner):
    jeton, brut = AppSendToken.issue(app, "prod")

    r = _as(owner).post(
        f"/api/v1/apps/{app.id}/send-tokens/{jeton.id}/reveal/",
        {"password": MOT_DE_PASSE},
        format="json",
    )

    assert r.status_code == 200
    assert r.json()["token"] == brut


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_revealing_demands_the_password_again(app, owner):
    """Une session ouverte ne suffit pas : c'est ce qui separe « relisible par
    toi » de « relisible par qui a atteint ta session »."""
    jeton, _ = AppSendToken.issue(app, "prod")

    r = _as(owner).post(
        f"/api/v1/apps/{app.id}/send-tokens/{jeton.id}/reveal/",
        {"password": "mauvais"},
        format="json",
    )

    assert r.status_code == 403


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_every_reveal_is_logged(app, owner):
    """Un jeton relisible sans trace serait relisible sans qu'on le sache."""
    jeton, _ = AppSendToken.issue(app, "prod")

    _as(owner).post(
        f"/api/v1/apps/{app.id}/send-tokens/{jeton.id}/reveal/",
        {"password": MOT_DE_PASSE},
        format="json",
    )

    trace = SendTokenReveal.objects.get()
    assert trace.token_id == jeton.id
    assert trace.actor_label == owner.email


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_a_stranger_cannot_reveal_someone_elses_token(app, db):
    autre = User.objects.create_user(email="autre@example.com", password=MOT_DE_PASSE)
    jeton, _ = AppSendToken.issue(app, "prod")

    r = _as(autre).post(
        f"/api/v1/apps/{app.id}/send-tokens/{jeton.id}/reveal/",
        {"password": MOT_DE_PASSE},
        format="json",
    )

    assert r.status_code == 404


# --------------------------------------------------------- le chiffrement

@pytest.mark.django_db
def test_without_a_key_the_token_works_but_cannot_be_read(app):
    """Le bon sens de la panne : perdre la cle coute la relecture, pas l'usage.
    L'authentification passe par l'empreinte, qui ne dechiffre rien."""
    with override_settings(APP_TOKEN_ENCRYPTION_KEYS=[]):
        jeton, brut = AppSendToken.issue(app, "prod")

        assert bytes(jeton.secret_encrypted) == b""
        with pytest.raises(RevealUnavailable):
            jeton.reveal()

    assert AppSendToken.objects.get(pk=jeton.pk).token_hash == AppSendToken.hash_raw(brut)


@pytest.mark.django_db
def test_the_secret_is_never_stored_in_clear(app):
    """Chiffre, pas en clair : un dump de base seul ne doit rien livrer."""
    with override_settings(**CHIFFREMENT):
        jeton, brut = AppSendToken.issue(app, "prod")

    stocke = bytes(AppSendToken.objects.get(pk=jeton.pk).secret_encrypted)
    assert brut.encode() not in stocke


@pytest.mark.django_db
def test_a_second_key_can_be_added_without_breaking_the_old_tokens(app):
    """Rotation : la nouvelle cle chiffre, l'ancienne dechiffre encore. Sans ce
    mecanisme, changer de cle rendrait d'un coup tous les jetons illisibles."""
    with override_settings(APP_TOKEN_ENCRYPTION_KEYS=[CLE]):
        jeton, brut = AppSendToken.issue(app, "prod")

    nouvelle = Fernet.generate_key().decode()
    with override_settings(APP_TOKEN_ENCRYPTION_KEYS=[nouvelle, CLE]):
        assert AppSendToken.objects.get(pk=jeton.pk).reveal() == brut


# ----------------------------------------------------------------- l'API

@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_creating_returns_the_value_once(app, owner):
    r = _as(owner).post(f"/api/v1/apps/{app.id}/send-tokens/", {"name": "prod"}, format="json")

    assert r.status_code == 201
    assert r.json()["token"].startswith("apt_")

    liste = _as(owner).get(f"/api/v1/apps/{app.id}/send-tokens/").json()
    assert "token" not in liste[0]


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_a_token_must_be_named(app, owner):
    """Sans nom, on ne sait pas lequel revoquer le jour ou il faut aller vite."""
    r = _as(owner).post(f"/api/v1/apps/{app.id}/send-tokens/", {"name": "  "}, format="json")

    assert r.status_code == 400


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_the_number_of_active_tokens_is_capped(app, owner):
    """Un compte compromis ne doit pas pouvoir en fabriquer mille."""
    for i in range(MAX_TOKENS_PER_APPLICATION):
        AppSendToken.issue(app, f"jeton-{i}")

    r = _as(owner).post(f"/api/v1/apps/{app.id}/send-tokens/", {"name": "de trop"}, format="json")

    assert r.status_code == 409


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_revoking_frees_a_slot(app, owner):
    jetons = [AppSendToken.issue(app, f"jeton-{i}")[0] for i in range(MAX_TOKENS_PER_APPLICATION)]

    _as(owner).delete(f"/api/v1/apps/{app.id}/send-tokens/{jetons[0].id}/")

    r = _as(owner).post(f"/api/v1/apps/{app.id}/send-tokens/", {"name": "remplacant"}, format="json")
    assert r.status_code == 201


@override_settings(**CHIFFREMENT)
@pytest.mark.django_db
def test_a_stranger_sees_nothing_and_creates_nothing(app, db):
    autre = User.objects.create_user(email="autre@example.com", password=MOT_DE_PASSE)

    assert _as(autre).get(f"/api/v1/apps/{app.id}/send-tokens/").status_code == 404
    assert _as(autre).post(
        f"/api/v1/apps/{app.id}/send-tokens/", {"name": "vol"}, format="json"
    ).status_code == 404
