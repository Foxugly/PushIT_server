"""Le code d'enrôlement : ce qu'il ouvre, et surtout ce qu'il n'ouvre pas.

Jusqu'ici un seul jeton faisait deux métiers opposés — il partait dans le QR
vers chaque destinataire ET il autorisait l'émission. Tout destinataire pouvait
donc écrire à tous les autres terminaux de l'application.

Le code d'enrôlement reprend le premier métier, et rien d'autre. C'est
l'invariant que ces tests protègent.
"""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application

VALID_PUSH_TOKEN = "token_12345678901234567890"


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="proprio@example.com", password="1234Test!!")


@pytest.fixture
def destinataire(db):
    return User.objects.create_user(email="destinataire@example.com", password="1234Test!!")


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


def _link(client, code, push_token=VALID_PUSH_TOKEN):
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


# --------------------------------------------------------------- le code existe

@pytest.mark.django_db
def test_every_application_gets_an_enrolment_code(app):
    assert app.enrolment_code.startswith("apk_")


@pytest.mark.django_db
def test_two_applications_never_share_a_code(owner):
    autre = Application(owner=owner, name="Autre")
    autre.set_new_app_token()
    autre.save()
    premiere = Application(owner=owner, name="Premiere")
    premiere.set_new_app_token()
    premiere.save()

    assert autre.enrolment_code != premiere.enrolment_code


@pytest.mark.django_db
def test_the_code_is_shorter_than_the_legacy_token(app):
    """Il est fait pour être recopié à la main ou lu sur un QR."""
    assert len(app.enrolment_code) < 30


# ------------------------------------------------------------------ il enrôle

@pytest.mark.django_db
def test_a_device_links_with_the_enrolment_code(app, destinataire):
    """C'est le remplacement du jeton dans le QR."""
    r = _link(_as(destinataire), app.enrolment_code)

    assert r.status_code == 200


@pytest.mark.django_db
def test_the_legacy_token_still_links_during_the_transition(owner, destinataire):
    """Les installations Play Store existantes portent l'ancien jeton : le
    refuser ici les couperait d'un coup."""
    application = Application(owner=owner, name="Heritee")
    ancien = application.set_new_app_token()
    application.save()

    r = _link(_as(destinataire), ancien)

    assert r.status_code == 200


@pytest.mark.django_db
def test_an_unknown_code_is_refused(app, destinataire):
    r = _link(_as(destinataire), "apk_inexistant")

    assert r.status_code == 401


@pytest.mark.django_db
def test_linking_still_requires_an_account(app):
    """Le code circule librement : c'est le compte qui reste la seconde barriere."""
    r = _link(APIClient(), app.enrolment_code)

    assert r.status_code == 401


# ------------------------------------------------- il n'ouvre RIEN d'autre

@pytest.mark.django_db
def test_the_enrolment_code_can_never_send(app, destinataire):
    """L'invariant central. Le code est distribué à chaque destinataire ; s'il
    autorisait l'émission, on aurait juste renommé le défaut."""
    r = APIClient().post(
        "/api/v1/notifications/app/send/",
        {"title": "x", "message": "y"},
        format="json",
        HTTP_X_APP_TOKEN=app.enrolment_code,
    )

    assert r.status_code == 401


@pytest.mark.django_db
def test_the_enrolment_code_can_never_list_notifications(app):
    """Lire les envois d'une application est aussi une fuite : le code ne doit
    ouvrir aucune route d'application, pas seulement pas l'émission."""
    r = APIClient().get("/api/v1/notifications/app/", HTTP_X_APP_TOKEN=app.enrolment_code)

    assert r.status_code == 401


# ---------------------------------------------------------------- la rotation

@pytest.mark.django_db
def test_rotating_the_code_invalidates_the_previous_one(app, destinataire):
    ancien = app.enrolment_code

    app.rotate_enrolment_code()
    app.save()

    assert _link(_as(destinataire), ancien).status_code == 401
    assert _link(_as(destinataire), app.enrolment_code).status_code == 200


@pytest.mark.django_db
def test_rotating_the_code_does_not_unlink_anyone(app, destinataire):
    """Le point que la console doit dire à l'utilisateur : changer le code ferme
    la porte aux futurs, il n'expulse personne. Pour retirer quelqu'un, il faut
    un autre geste."""
    _link(_as(destinataire), app.enrolment_code)
    lies_avant = app.device_links.filter(is_active=True).count()

    app.rotate_enrolment_code()
    app.save()

    assert lies_avant == 1
    assert app.device_links.filter(is_active=True).count() == 1


@pytest.mark.django_db
def test_rotating_stamps_the_date(app):
    assert app.enrolment_code_rotated_at is None

    app.rotate_enrolment_code()
    app.save()

    assert app.enrolment_code_rotated_at is not None
