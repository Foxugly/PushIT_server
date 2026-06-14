import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus
from notifications.models import NotificationDelivery


def _auth_client_for(user: User) -> APIClient:
    client = APIClient()
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


def _create_linked_device(app: Application, token: str) -> Device:
    device = Device.objects.create(
        push_token=token,
        push_token_status=DeviceTokenStatus.ACTIVE,
    )
    DeviceApplicationLink.objects.create(device=device, application=app)
    return device


@pytest.mark.django_db
def test_create_notification_with_device_list_creates_target_deliveries():
    user = User.objects.create_user(
        email="notify@example.com",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Notify App")
    device1 = _create_linked_device(app, "token_notify_11111111111111111111")
    device2 = _create_linked_device(app, "token_notify_22222222222222222222")
    client = _auth_client_for(user)

    response = client.post(
        "/api/v1/notifications/",
        {
            "application_id": app.id,
            "device_ids": [device2.id, device1.id],
            "title": "Promo flash",
            "message": "Disponible maintenant.",
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["device_ids"] == [device1.id, device2.id]
    assert NotificationDelivery.objects.count() == 2
    assert list(
        NotificationDelivery.objects.order_by("device_id").values_list("device_id", flat=True)
    ) == [device1.id, device2.id]


@pytest.mark.django_db
def test_create_notification_rejects_device_outside_selected_application():
    user = User.objects.create_user(
        email="notify-invalid@example.com",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Notify App")
    other_app = Application.objects.create(owner=user, name="Other App")
    _create_linked_device(app, "token_notify_33333333333333333333")
    foreign_device = _create_linked_device(other_app, "token_notify_44444444444444444444")
    client = _auth_client_for(user)

    response = client.post(
        "/api/v1/notifications/",
        {
            "application_id": app.id,
            "device_ids": [foreign_device.id],
            "title": "Promo flash",
            "message": "Disponible maintenant.",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert "device_ids" in response.data["errors"]


@pytest.mark.django_db
def test_notification_detail_includes_per_device_deliveries():
    from notifications.models import DeliveryStatus, Notification, NotificationStatus

    user = User.objects.create_user(
        email="detail@example.com",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Detail App")
    device1 = _create_linked_device(app, "token_detail_11111111111111111111")
    device2 = _create_linked_device(app, "token_detail_22222222222222222222")
    notif = Notification.objects.create(
        application=app, title="T", message="M", status=NotificationStatus.PARTIAL
    )
    NotificationDelivery.objects.create(notification=notif, device=device1, status=DeliveryStatus.SENT)
    NotificationDelivery.objects.create(notification=notif, device=device2, status=DeliveryStatus.FAILED)

    client = _auth_client_for(user)
    response = client.get(f"/api/v1/notifications/{notif.id}/")

    assert response.status_code == 200
    deliveries = {d["device_id"]: d["status"] for d in response.data["deliveries"]}
    assert deliveries == {device1.id: DeliveryStatus.SENT, device2.id: DeliveryStatus.FAILED}


@pytest.mark.django_db
def test_notifications_list_is_bare_array_by_default_and_paginates_on_demand():
    from notifications.models import Notification, NotificationStatus

    user = User.objects.create_user(
        email="page@example.com", password="MotDePasseTresSolide123!"
    )
    app = Application.objects.create(owner=user, name="Page App")
    for i in range(3):
        Notification.objects.create(
            application=app, title=f"n{i}", message="m", status=NotificationStatus.DRAFT
        )
    client = _auth_client_for(user)

    # Default: bare array (backward compatible with SPA + mobile).
    bare = client.get("/api/v1/notifications/")
    assert bare.status_code == 200
    assert isinstance(bare.data, list)
    assert len(bare.data) == 3

    # Opt-in pagination via ?page → envelope.
    paged = client.get("/api/v1/notifications/", {"page": 1, "page_size": 2})
    assert paged.status_code == 200
    assert paged.data["count"] == 3
    assert len(paged.data["results"]) == 2
    assert paged.data["next"] is not None
