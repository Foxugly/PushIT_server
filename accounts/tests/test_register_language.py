"""Audit 2026-10-19 — item 7: register accepts an optional `language`."""
import pytest
from rest_framework.test import APIClient

from accounts.models import User

URL = "/api/v1/auth/register/"


@pytest.mark.django_db
def test_register_stores_language():
    r = APIClient().post(
        URL, {"email": "nl@example.com", "password": "MotDePasseTresSolide123!", "language": "NL"}, format="json"
    )
    assert r.status_code == 201, r.content
    assert User.objects.get(email="nl@example.com").language == "NL"


@pytest.mark.django_db
def test_register_language_is_case_insensitive():
    r = APIClient().post(
        URL, {"email": "en@example.com", "password": "MotDePasseTresSolide123!", "language": "en"}, format="json"
    )
    assert r.status_code == 201, r.content
    assert User.objects.get(email="en@example.com").language == "EN"


@pytest.mark.django_db
def test_register_rejects_unsupported_language():
    r = APIClient().post(
        URL, {"email": "de@example.com", "password": "MotDePasseTresSolide123!", "language": "DE"}, format="json"
    )
    assert r.status_code == 400
    assert "language" in r.json()["errors"]
    assert not User.objects.filter(email="de@example.com").exists()


@pytest.mark.django_db
def test_register_without_language_keeps_default():
    r = APIClient().post(URL, {"email": "x@example.com", "password": "MotDePasseTresSolide123!"}, format="json")
    assert r.status_code == 201
    assert User.objects.get(email="x@example.com").language == "FR"
