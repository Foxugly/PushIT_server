from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from accounts.models import User
from applications.models import Application
from notifications.models import InboundEmailIngestionLog, Notification


@pytest.mark.django_db
@override_settings(INBOUND_EMAIL_SECRET="test-inbound-secret", INBOUND_EMAIL_DOMAIN="pushit.com")
def test_inbound_email_creates_scheduled_notification_from_subject_marker():
    client = APIClient()
    user = User.objects.create_user(
        email="inbound@example.com",
        username="inbound",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Inbound App")
    scheduled_for = timezone.now() + timedelta(hours=2)

    response = client.post(
        "/api/v1/notifications/inbound/email/",
        {
            "sender": user.email,
            "recipient": f"{app.inbound_email_alias}@pushit.com",
            "subject": f"Maintenance [SEND_AT:{scheduled_for.isoformat()}]",
            "text": "Maintenance ce soir.",
            "message_id": "mail-001@example.com",
        },
        format="json",
        HTTP_X_INBOUND_EMAIL_SECRET="test-inbound-secret",
    )

    assert response.status_code == 201
    assert response.data["application_id"] == app.id
    assert response.data["title"] == "Maintenance"
    assert response.data["message"] == "Maintenance ce soir."
    assert parse_datetime(response.data["scheduled_for"]) == scheduled_for
    assert Notification.objects.count() == 1
    log = InboundEmailIngestionLog.objects.get()
    assert log.status == "created"
    assert log.application_id == app.id


@pytest.mark.django_db
@override_settings(INBOUND_EMAIL_SECRET="test-inbound-secret", INBOUND_EMAIL_DOMAIN="pushit.com")
def test_inbound_email_is_idempotent_with_same_message_id():
    client = APIClient()
    user = User.objects.create_user(
        email="inbound-idem@example.com",
        username="inbound-idem",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Inbound App")
    payload = {
        "sender": user.email,
        "recipient": f"{app.inbound_email_alias}@pushit.com",
        "subject": "Alerte production",
        "text": "Le batch est termine.",
        "message_id": "mail-002@example.com",
    }

    first_response = client.post(
        "/api/v1/notifications/inbound/email/",
        payload,
        format="json",
        HTTP_X_INBOUND_EMAIL_SECRET="test-inbound-secret",
    )
    second_response = client.post(
        "/api/v1/notifications/inbound/email/",
        payload,
        format="json",
        HTTP_X_INBOUND_EMAIL_SECRET="test-inbound-secret",
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    assert first_response.data["id"] == second_response.data["id"]
    assert Notification.objects.count() == 1
    assert list(InboundEmailIngestionLog.objects.order_by("id").values_list("status", flat=True)) == [
        "created",
        "existing",
    ]


@pytest.mark.django_db
@override_settings(INBOUND_EMAIL_SECRET="test-inbound-secret", INBOUND_EMAIL_DOMAIN="pushit.com")
def test_inbound_email_rejects_invalid_subject_marker():
    client = APIClient()
    user = User.objects.create_user(
        email="inbound-invalid@example.com",
        username="inbound-invalid",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Inbound App")

    response = client.post(
        "/api/v1/notifications/inbound/email/",
        {
            "sender": user.email,
            "recipient": f"{app.inbound_email_alias}@pushit.com",
            "subject": "Maintenance [SEND_AT:demain 20h]",
            "text": "Maintenance ce soir.",
        },
        format="json",
        HTTP_X_INBOUND_EMAIL_SECRET="test-inbound-secret",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert "subject" in response.data["errors"]
    assert InboundEmailIngestionLog.objects.get().status == "rejected"


@pytest.mark.django_db
@override_settings(INBOUND_EMAIL_SECRET="test-inbound-secret", INBOUND_EMAIL_DOMAIN="pushit.com")
def test_inbound_email_rejects_unknown_sender():
    client = APIClient()
    user = User.objects.create_user(
        email="inbound-owner@example.com",
        username="inbound-owner",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Inbound App")

    response = client.post(
        "/api/v1/notifications/inbound/email/",
        {
            "sender": "unknown@example.com",
            "recipient": f"{app.inbound_email_alias}@pushit.com",
            "subject": "Alerte production",
            "text": "Le batch est termine.",
        },
        format="json",
        HTTP_X_INBOUND_EMAIL_SECRET="test-inbound-secret",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert "sender" in response.data["errors"]
    assert InboundEmailIngestionLog.objects.get().status == "rejected"


@pytest.mark.django_db
@override_settings(INBOUND_EMAIL_SECRET="test-inbound-secret", INBOUND_EMAIL_DOMAIN="pushit.com")
def test_inbound_email_rejects_sender_not_owning_target_app():
    client = APIClient()
    owner = User.objects.create_user(
        email="owner@example.com",
        username="owner",
        password="MotDePasseTresSolide123!",
    )
    other_user = User.objects.create_user(
        email="other@example.com",
        username="other",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=owner, name="Inbound App")

    response = client.post(
        "/api/v1/notifications/inbound/email/",
        {
            "sender": other_user.email,
            "recipient": f"{app.inbound_email_alias}@pushit.com",
            "subject": "Alerte production",
            "text": "Le batch est termine.",
        },
        format="json",
        HTTP_X_INBOUND_EMAIL_SECRET="test-inbound-secret",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert "sender" in response.data["errors"]
    assert InboundEmailIngestionLog.objects.get().status == "rejected"
