import pytest
from concurrent.futures import ThreadPoolExecutor

import requests
from django.db import transaction
from rest_framework.test import APIClient
from accounts.models import User
from applications.models import Application
from notifications.models import Notification


@pytest.mark.django_db
def test_create_notification_with_app_token():
    client = APIClient()

    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Mon App")
    raw_token = app.set_new_app_token()
    app.save()

    response = client.post(
        "/api/v1/notifications/app/create/",
        {
            "title": "Alerte",
            "message": "Hello",
        },
        format="json",
        HTTP_X_APP_TOKEN=raw_token,
        HTTP_IDEMPOTENCY_KEY="test-idem-001",
    )

    assert response.status_code == 201
    assert Notification.objects.count() == 1
    notification = Notification.objects.first()
    assert notification.title == "Alerte"
    assert notification.message == "Hello"
    assert notification.idempotency_key == "test-idem-001"

@pytest.mark.django_db
def test_create_notification_with_same_idempotency_key_returns_existing_notification():
    client = APIClient()

    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Mon App")
    raw_token = app.set_new_app_token()
    app.save()

    payload = {
        "title": "Alerte",
        "message": "Hello",
    }

    response1 = client.post(
        "/api/v1/notifications/app/create/",
        payload,
        format="json",
        HTTP_X_APP_TOKEN=raw_token,
        HTTP_IDEMPOTENCY_KEY="idem-123",
    )

    response2 = client.post(
        "/api/v1/notifications/app/create/",
        payload,
        format="json",
        HTTP_X_APP_TOKEN=raw_token,
        HTTP_IDEMPOTENCY_KEY="idem-123",
    )

    assert response1.status_code == 201
    assert response2.status_code == 200
    assert Notification.objects.count() == 1
    assert response1.data["id"] == response2.data["id"]

@pytest.mark.django_db
def test_create_notification_with_same_idempotency_key_and_different_payload_returns_409():
    client = APIClient()

    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Mon App")
    raw_token = app.set_new_app_token()
    app.save()

    response1 = client.post(
        "/api/v1/notifications/app/create/",
        {
            "title": "Alerte",
            "message": "Hello",
        },
        format="json",
        HTTP_X_APP_TOKEN=raw_token,
        HTTP_IDEMPOTENCY_KEY="idem-123",
    )

    response2 = client.post(
        "/api/v1/notifications/app/create/",
        {
            "title": "Alerte modifiée",
            "message": "Hello 2",
        },
        format="json",
        HTTP_X_APP_TOKEN=raw_token,
        HTTP_IDEMPOTENCY_KEY="idem-123",
    )

    assert response1.status_code == 201
    assert response2.status_code == 409
    assert response2.data["code"] == "idempotency_conflict"
    assert Notification.objects.count() == 1


@pytest.mark.django_db
def test_create_notification_without_idempotency_key_returns_400():
    client = APIClient()

    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Mon App")
    raw_token = app.set_new_app_token()
    app.save()

    response = client.post(
        "/api/v1/notifications/app/create/",
        {
            "title": "Alerte",
            "message": "Hello",
        },
        format="json",
        HTTP_X_APP_TOKEN=raw_token,
    )

    assert response.status_code == 400
    assert response.data["code"] == "idempotency_key_missing"
    assert response.data["detail"] == "Header Idempotency-Key manquant."


@pytest.mark.django_db(transaction=True)
def test_concurrent_create_notification_with_same_idempotency_key_is_idempotent(live_server):
    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Mon App")
    raw_token = app.set_new_app_token()
    app.save()
    transaction.commit()

    url = f"{live_server.url}/api/v1/notifications/app/create/"
    payload = {
        "title": "Alerte",
        "message": "Hello",
    }
    headers = {
        "X-App-Token": raw_token,
        "Idempotency-Key": "idem-concurrent-123",
    }

    warmup_response = requests.get(
        f"{live_server.url}/api/v1/notifications/app/",
        headers={"X-App-Token": raw_token},
        timeout=10,
    )
    assert warmup_response.status_code == 200

    def do_request():
        return requests.post(url, json=payload, headers=headers, timeout=10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: do_request(), range(2)))

    status_codes = sorted(response.status_code for response in responses)

    assert status_codes == [200, 201]
    assert Notification.objects.count() == 1

    body_ids = {response.json()["id"] for response in responses}
    assert len(body_ids) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_create_notification_with_same_idempotency_key_and_different_payload_conflicts(live_server):
    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Mon App")
    raw_token = app.set_new_app_token()
    app.save()
    transaction.commit()

    url = f"{live_server.url}/api/v1/notifications/app/create/"
    headers = {
        "X-App-Token": raw_token,
        "Idempotency-Key": "idem-concurrent-conflict",
    }
    warmup_response = requests.get(
        f"{live_server.url}/api/v1/notifications/app/",
        headers={"X-App-Token": raw_token},
        timeout=10,
    )
    assert warmup_response.status_code == 200
    payloads = [
        {"title": "Alerte", "message": "Hello"},
        {"title": "Alerte modifiée", "message": "Hello 2"},
    ]

    def do_request(payload):
        return requests.post(url, json=payload, headers=headers, timeout=10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(do_request, payloads))

    status_codes = sorted(response.status_code for response in responses)

    assert status_codes == [201, 409]
    assert "idempotency_conflict" in {response.json().get("code") for response in responses}
    assert Notification.objects.count() == 1
