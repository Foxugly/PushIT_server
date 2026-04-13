import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from notifications.models import Notification, NotificationTemplate, NotificationStatus
from devices.models import Device, DeviceApplicationLink


@pytest.fixture
def auth_context():
    user = User.objects.create_user(
        email="tpl@example.com",
        username="tpl",
        password="StrongPass123!",
    )
    app = Application.objects.create(owner=user, name="Template App")
    client = APIClient()
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return user, app, client


@pytest.mark.django_db
def test_create_list_update_delete_template(auth_context):
    user, app, client = auth_context

    # Create
    create_resp = client.post(
        f"/api/v1/apps/{app.id}/templates/",
        {
            "name": "welcome",
            "title_template": "Hello {{username}}",
            "message_template": "Welcome {{username}} to {{app_name}}!",
        },
        format="json",
    )
    assert create_resp.status_code == 201
    template_id = create_resp.data["id"]
    assert create_resp.data["name"] == "welcome"
    assert create_resp.data["title_template"] == "Hello {{username}}"

    # List
    list_resp = client.get(f"/api/v1/apps/{app.id}/templates/")
    assert list_resp.status_code == 200
    assert list_resp.data["count"] == 1

    # Update
    patch_resp = client.patch(
        f"/api/v1/apps/{app.id}/templates/{template_id}/",
        {"name": "welcome-v2"},
        format="json",
    )
    assert patch_resp.status_code == 200
    assert patch_resp.data["name"] == "welcome-v2"

    # Delete
    delete_resp = client.delete(f"/api/v1/apps/{app.id}/templates/{template_id}/")
    assert delete_resp.status_code == 204

    list_resp = client.get(f"/api/v1/apps/{app.id}/templates/")
    assert list_resp.data["count"] == 0


@pytest.mark.django_db
def test_template_not_found_returns_404(auth_context):
    user, app, client = auth_context

    resp = client.get(f"/api/v1/apps/{app.id}/templates/999/")
    assert resp.status_code == 404
    assert resp.data["code"] == "template_not_found"


@pytest.mark.django_db
def test_template_duplicate_name_rejected(auth_context):
    user, app, client = auth_context

    client.post(
        f"/api/v1/apps/{app.id}/templates/",
        {"name": "dup", "title_template": "T", "message_template": "M"},
        format="json",
    )
    resp = client.post(
        f"/api/v1/apps/{app.id}/templates/",
        {"name": "dup", "title_template": "T2", "message_template": "M2"},
        format="json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_model_render():
    user = User.objects.create_user(
        email="render@example.com", username="render", password="StrongPass123!",
    )
    app = Application.objects.create(owner=user, name="Render App")
    tpl = NotificationTemplate.objects.create(
        application=app,
        name="test",
        title_template="Hello {{name}}",
        message_template="Your code is {{code}}, welcome {{name}}!",
    )
    title, message = tpl.render({"name": "Alice", "code": "1234"})
    assert title == "Hello Alice"
    assert message == "Your code is 1234, welcome Alice!"


@pytest.mark.django_db
def test_model_render_no_variables():
    user = User.objects.create_user(
        email="render2@example.com", username="render2", password="StrongPass123!",
    )
    app = Application.objects.create(owner=user, name="Render App 2")
    tpl = NotificationTemplate.objects.create(
        application=app,
        name="static",
        title_template="Fixed title",
        message_template="Fixed message",
    )
    title, message = tpl.render()
    assert title == "Fixed title"
    assert message == "Fixed message"


@pytest.mark.django_db
def test_create_notification_from_template(auth_context):
    user, app, client = auth_context

    tpl = NotificationTemplate.objects.create(
        application=app,
        name="promo",
        title_template="{{percent}}% off!",
        message_template="Get {{percent}}% off on {{product}}.",
    )

    device = Device.objects.create(
        push_token="token_" + "x" * 30,
        device_name="Test Device",
        platform="android",
    )
    DeviceApplicationLink.objects.create(device=device, application=app, is_active=True)

    resp = client.post(
        "/api/v1/notifications/",
        {
            "application_id": app.id,
            "device_ids": [device.id],
            "template_id": tpl.id,
            "variables": {"percent": "50", "product": "PushIT Pro"},
        },
        format="json",
    )
    assert resp.status_code == 201
    assert resp.data["title"] == "50% off!"
    assert resp.data["message"] == "Get 50% off on PushIT Pro."

    notification = Notification.objects.get(id=resp.data["id"])
    assert notification.title == "50% off!"
    assert notification.status == NotificationStatus.DRAFT


@pytest.mark.django_db
def test_create_notification_rejects_template_with_title(auth_context):
    user, app, client = auth_context

    tpl = NotificationTemplate.objects.create(
        application=app,
        name="conflict",
        title_template="T",
        message_template="M",
    )

    device = Device.objects.create(
        push_token="token_" + "y" * 30,
        device_name="Test Device",
        platform="android",
    )
    DeviceApplicationLink.objects.create(device=device, application=app, is_active=True)

    resp = client.post(
        "/api/v1/notifications/",
        {
            "application_id": app.id,
            "device_ids": [device.id],
            "template_id": tpl.id,
            "title": "Conflict",
            "message": "Conflict",
        },
        format="json",
    )
    assert resp.status_code == 400
    assert "template_id" in resp.data.get("errors", {})


@pytest.mark.django_db
def test_create_notification_requires_title_or_template(auth_context):
    user, app, client = auth_context

    device = Device.objects.create(
        push_token="token_" + "z" * 30,
        device_name="Test Device",
        platform="android",
    )
    DeviceApplicationLink.objects.create(device=device, application=app, is_active=True)

    resp = client.post(
        "/api/v1/notifications/",
        {
            "application_id": app.id,
            "device_ids": [device.id],
        },
        format="json",
    )
    assert resp.status_code == 400
