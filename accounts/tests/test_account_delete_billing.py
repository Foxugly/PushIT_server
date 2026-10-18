"""DELETE /api/v1/auth/me/ erases the user at the central billing service FIRST.

The central (`POST /api/v1/erase/`, billing_server #52) cancels the user's
subscriptions immediately, deletes the Stripe customer and anonymises its
records. If it cannot do so (502/503, unreachable, or any other refusal), the
local account is kept and the client gets 503 `billing_unavailable` so the user
can retry: deleting locally first would leave a payer billed with no account
left to reach the Stripe portal.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings
from rest_framework.test import APIClient

from accounts.models import User

URL = "/api/v1/auth/me/"
PASSWORD = "MotDePasseTresSolide123!"
BILLING_ON = {
    "BILLING_BASE_URL": "https://billing-api.foxugly.com",
    "BILLING_APP_SECRET": "secret-de-test",
    "BILLING_APP_SLUG": "pushit",
}


def _user():
    return User.objects.create_user(email="erase@example.com", password=PASSWORD, email_confirmed=True)


def _delete(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.delete(URL, {"current_password": PASSWORD}, format="json")


def _response(status_code, body=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body if body is not None else {}
    if body is None and status_code == 204:
        response.json.side_effect = ValueError("no body")
    return response


@pytest.mark.django_db
@override_settings(**BILLING_ON)
@pytest.mark.parametrize("status_code,body", [(200, {"canceled": ["sub_1"]}), (204, None)])
def test_billing_erased_then_user_deleted(status_code, body):
    user = _user()
    user_id = user.pk
    with patch("billing.client.requests.post", return_value=_response(status_code, body)) as post:
        response = _delete(user)

    assert response.status_code == 204
    assert not User.objects.filter(pk=user_id).exists()
    post.assert_called_once()
    url = post.call_args.args[0]
    assert url == "https://billing-api.foxugly.com/api/v1/erase/"
    kwargs = post.call_args.kwargs
    # Same id as the checkout: str(user.pk).
    assert json.loads(kwargs["data"]) == {"external_user_id": str(user_id)}
    assert kwargs["headers"]["X-Foxugly-App"] == "pushit"
    assert kwargs["headers"]["X-Foxugly-Signature"].startswith("sha256=")
    assert kwargs["timeout"]


@pytest.mark.django_db
@override_settings(**BILLING_ON)
@pytest.mark.parametrize("status_code", [502, 503, 401, 400])
def test_billing_failure_keeps_user_and_returns_503(status_code):
    user = _user()
    with patch(
        "billing.client.requests.post",
        return_value=_response(status_code, {"code": "stripe_error", "detail": "x"}),
    ):
        response = _delete(user)

    assert response.status_code == 503
    assert response.json()["code"] == "billing_unavailable"
    assert User.objects.filter(pk=user.pk).exists()


@pytest.mark.django_db
@override_settings(**BILLING_ON)
def test_billing_timeout_keeps_user_and_returns_503():
    user = _user()
    with patch("billing.client.requests.post", side_effect=requests.Timeout("slow")):
        response = _delete(user)

    assert response.status_code == 503
    assert response.json()["code"] == "billing_unavailable"
    assert User.objects.filter(pk=user.pk).exists()


@pytest.mark.django_db
@override_settings(**BILLING_ON)
def test_wrong_password_never_reaches_billing():
    user = _user()
    client = APIClient()
    client.force_authenticate(user=user)
    with patch("billing.client.requests.post") as post:
        response = client.delete(URL, {"current_password": "nope"}, format="json")
    assert response.status_code == 400
    post.assert_not_called()


@pytest.mark.django_db
def test_billing_unconfigured_skips_the_call():
    user = _user()
    with patch("billing.client.requests.post") as post:
        response = _delete(user)
    assert response.status_code == 204
    post.assert_not_called()
    assert not User.objects.exists()
