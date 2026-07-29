"""Expulser un abonné d'une application.

Le trou que ces tests ferment : jusqu'ici, le propriétaire d'une application ne
pouvait retirer personne. `/devices/unlink/` délie le terminal *de l'appelant*,
`/devices/unlink-app/` sert au destinataire qui se désabonne — les deux
supposent qu'on est du côté du téléphone. Et changer le code d'enrôlement ne
délie personne : il ferme la porte aux futurs, pas aux présents.

Restait le geste de trop : désactiver toute l'application pour retirer un seul
indésirable.
"""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, UnlinkSource


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="proprio@example.com", password="1234Test!!")


@pytest.fixture
def intrus(db):
    return User.objects.create_user(email="intrus@example.com", password="1234Test!!")


def _application(owner, name="App"):
    application = Application(owner=owner, name=name)
    application.set_new_app_token()
    application.save()
    return application


@pytest.fixture
def app(owner):
    return _application(owner)


@pytest.fixture
def other_app(owner):
    return _application(owner, name="Autre")


@pytest.fixture
def foreign_device(intrus):
    """Le terminal d'un tiers : il appartient à l'intrus, pas au propriétaire."""
    return Device.objects.create(
        user=intrus, device_name="Pixel", platform="android",
        push_token="token_12345678901234567890",
    )


def _link(device, application):
    return DeviceApplicationLink.objects.create(device=device, application=application)


def _as(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


@pytest.fixture
def owner_client(owner):
    return _as(owner)


def _url(application, device):
    return f"/api/v1/apps/{application.id}/devices/{device.id}/"


# ------------------------------------------------------------------ le geste

@pytest.mark.django_db
def test_the_owner_can_evict_a_linked_device(owner_client, app, foreign_device):
    """Changer le code d'enrôlement ne délie personne : sans cette route, un
    indésirable ne se retire qu'en désactivant toute l'application."""
    _link(foreign_device, app)

    r = owner_client.delete(_url(app, foreign_device))

    assert r.status_code == 204
    assert not app.device_links.filter(device=foreign_device, is_active=True).exists()


@pytest.mark.django_db
def test_evicting_keeps_the_device_itself(owner_client, app, foreign_device):
    """Le terminal appartient à quelqu'un d'autre : on coupe le lien, on ne
    supprime pas son téléphone."""
    _link(foreign_device, app)

    owner_client.delete(_url(app, foreign_device))

    assert Device.objects.filter(id=foreign_device.id).exists()


@pytest.mark.django_db
def test_evicting_is_recorded_as_such(owner_client, app, foreign_device):
    """La cause du délien se lit après coup : un désabonnement volontaire et une
    expulsion ne se racontent pas pareil au support."""
    _link(foreign_device, app)

    owner_client.delete(_url(app, foreign_device))

    lien = app.device_links.get(device=foreign_device)
    assert lien.unlink_source == UnlinkSource.OWNER_EVICTION
    assert lien.unlinked_at is not None


@pytest.mark.django_db
def test_an_evicted_device_stops_receiving(owner_client, app, foreign_device):
    """La conséquence attendue, vérifiée sur la requête qui sert réellement à
    l'envoi — pas seulement sur le drapeau."""
    _link(foreign_device, app)
    cibles = Device.objects.filter(
        application_links__application=app, application_links__is_active=True
    )
    assert foreign_device in cibles

    owner_client.delete(_url(app, foreign_device))

    assert foreign_device not in cibles.all()


# ---------------------------------------------------------------- la portée

@pytest.mark.django_db
def test_evicting_touches_only_this_application(owner_client, app, other_app, foreign_device):
    """Le terminal peut être rattaché à plusieurs applications : en expulser d'une
    ne doit pas le couper des autres, qui ne regardent pas ce propriétaire."""
    _link(foreign_device, app)
    _link(foreign_device, other_app)

    owner_client.delete(_url(app, foreign_device))

    assert other_app.device_links.filter(device=foreign_device, is_active=True).exists()


@pytest.mark.django_db
def test_a_stranger_cannot_evict_from_an_application_they_do_not_own(intrus, app, foreign_device):
    _link(foreign_device, app)

    r = _as(intrus).delete(_url(app, foreign_device))

    assert r.status_code in (403, 404)
    assert app.device_links.filter(device=foreign_device, is_active=True).exists()


@pytest.mark.django_db
def test_evicting_requires_an_account(app, foreign_device):
    _link(foreign_device, app)

    r = APIClient().delete(_url(app, foreign_device))

    assert r.status_code == 401


@pytest.mark.django_db
def test_a_device_that_was_never_linked_is_not_found(owner_client, app, foreign_device):
    """Sinon la console prétendrait avoir retiré quelqu'un qui n'était pas là."""
    r = owner_client.delete(_url(app, foreign_device))

    assert r.status_code == 404


@pytest.mark.django_db
def test_evicting_twice_stays_calm(owner_client, app, foreign_device):
    """Un double clic, un onglet resté ouvert : le second appel ne doit pas
    ressembler à une panne."""
    _link(foreign_device, app)

    premier = owner_client.delete(_url(app, foreign_device))
    second = owner_client.delete(_url(app, foreign_device))

    assert premier.status_code == 204
    assert second.status_code == 204


# ------------------------------------------------------- ce qu'elle ne dit pas

@pytest.mark.django_db
def test_an_evicted_device_can_come_back_with_the_same_code(owner_client, app, foreign_device, intrus):
    """L'expulsion seule ne suffit pas : tant que le code d'enrôlement n'a pas
    tourné, l'expulsé le connaît encore et se réenrôle. C'est exactement ce que
    la console doit dire — expulser, puis faire tourner le code."""
    _link(foreign_device, app)
    owner_client.delete(_url(app, foreign_device))

    r = _as(intrus).post(
        "/api/v1/devices/link/",
        {
            "app_token": app.enrolment_code,
            "device_name": "Pixel",
            "platform": "android",
            "push_token": foreign_device.push_token,
        },
        format="json",
    )

    assert r.status_code == 200
    assert app.device_links.filter(device=foreign_device, is_active=True).exists()
