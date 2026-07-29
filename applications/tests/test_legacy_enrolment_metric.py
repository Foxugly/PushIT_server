"""La mesure qui conditionne la suppression des colonnes héritées.

Sans elle, l'entrée B3 du backlog ne peut jamais être déclenchée : on attend que
« plus aucun terminal ne dépende de l'enrôlement hérité » sans avoir de quoi le
constater. Seuls les échecs de ce chemin étaient comptés.
"""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application


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


@pytest.mark.django_db
def test_a_legacy_enrolment_is_counted(app, destinataire, monkeypatch):
    """C'est ce compteur qui dira un jour qu'on peut couper."""
    vus = []
    monkeypatch.setattr(
        "applications.authentication.increment_counter",
        lambda nom, labels=None: vus.append((nom, (labels or {}).get("outcome"))),
    )
    ancien = app.set_new_app_token()
    app.save(update_fields=["app_token_prefix", "app_token_hash", "revoked_at", "last_used_at"])

    assert _link(_as(destinataire), ancien).status_code == 200

    assert ("pushit_app_token_auth_total", "legacy_enrolment") in vus


@pytest.mark.django_db
def test_an_enrolment_with_the_code_is_counted_apart(app, destinataire, monkeypatch):
    """Les deux chemins doivent se distinguer, sinon la mesure ne decide rien."""
    vus = []
    monkeypatch.setattr(
        "applications.authentication.increment_counter",
        lambda nom, labels=None: vus.append((nom, (labels or {}).get("outcome"))),
    )

    assert _link(_as(destinataire), app.enrolment_code).status_code == 200

    issues = [issue for _, issue in vus]
    assert "enrolment_code" in issues
    assert "legacy_enrolment" not in issues
