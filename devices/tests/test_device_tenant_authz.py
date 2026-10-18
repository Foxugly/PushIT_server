"""Cross-tenant authorization on device endpoints.

An application owner sees the devices subscribed to their apps (read-only, for
the console), but only the device's own user may modify it, delete it or manage
its quiet periods — a device is shared across every app it is linked to. The
owner's read view must not disclose other owners' applications either.
"""

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from devices.models import (
    Device,
    DeviceApplicationLink,
    DeviceQuietPeriod,
    DeviceTokenStatus,
    UnlinkSource,
)

PASSWORD = "MotDePasseTresSolide123!"


def _client(user: User) -> APIClient:
    client = APIClient()
    access = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


@pytest.fixture
def tenants():
    owner_a = User.objects.create_user(email="owner-a@example.com", password=PASSWORD)
    owner_c = User.objects.create_user(email="owner-c@example.com", password=PASSWORD)
    subscriber_b = User.objects.create_user(email="subscriber-b@example.com", password=PASSWORD)
    app_a = Application.objects.create(owner=owner_a, name="A app")
    app_c = Application.objects.create(owner=owner_c, name="C secret app")
    app_c_old = Application.objects.create(owner=owner_c, name="C old app")
    device = Device.objects.create(
        user=subscriber_b,
        device_name="B phone",
        push_token="fcm_tenant_b",
        push_token_status=DeviceTokenStatus.ACTIVE,
    )
    DeviceApplicationLink.objects.create(device=device, application=app_a)
    DeviceApplicationLink.objects.create(device=device, application=app_c)
    DeviceApplicationLink.objects.create(
        device=device,
        application=app_c_old,
        is_active=False,
        unlink_source=UnlinkSource.INBOX,
    )
    quiet_period = DeviceQuietPeriod.objects.create(
        device=device,
        name="Night",
        period_type="RECURRING",
        recurrence_days=[0, 1, 2],
        start_time="22:00:00",
        end_time="07:00:00",
    )
    return {
        "owner_a": owner_a,
        "owner_c": owner_c,
        "subscriber_b": subscriber_b,
        "app_a": app_a,
        "app_c": app_c,
        "app_c_old": app_c_old,
        "device": device,
        "quiet_period": quiet_period,
    }


# --- writes on the device: only its own user -------------------------------


@pytest.mark.django_db
def test_app_owner_cannot_patch_subscriber_device(tenants):
    device = tenants["device"]
    resp = _client(tenants["owner_a"]).patch(
        f"/api/v1/devices/{device.id}/", {"push_token_status": "revoked"}, format="json"
    )
    assert resp.status_code == 404
    assert resp.data["code"] == "device_not_found"
    device.refresh_from_db()
    assert device.push_token_status == DeviceTokenStatus.ACTIVE


@pytest.mark.django_db
def test_app_owner_cannot_put_subscriber_device(tenants):
    device = tenants["device"]
    resp = _client(tenants["owner_a"]).put(
        f"/api/v1/devices/{device.id}/",
        {"device_name": "pwned", "platform": "ios", "push_token_status": "revoked"},
        format="json",
    )
    assert resp.status_code == 404
    device.refresh_from_db()
    assert device.device_name == "B phone"
    assert device.push_token_status == DeviceTokenStatus.ACTIVE


@pytest.mark.django_db
def test_app_owner_cannot_delete_subscriber_device(tenants):
    device = tenants["device"]
    resp = _client(tenants["owner_a"]).delete(f"/api/v1/devices/{device.id}/")
    assert resp.status_code == 404
    assert Device.objects.filter(id=device.id).exists()
    assert DeviceApplicationLink.objects.filter(device=device).count() == 3


@pytest.mark.django_db
def test_device_user_can_still_update_and_delete_own_device(tenants):
    device = tenants["device"]
    client = _client(tenants["subscriber_b"])
    resp = client.patch(f"/api/v1/devices/{device.id}/", {"device_name": "Renamed"}, format="json")
    assert resp.status_code == 200
    device.refresh_from_db()
    assert device.device_name == "Renamed"
    resp = client.delete(f"/api/v1/devices/{device.id}/")
    assert resp.status_code == 204
    assert not Device.objects.filter(id=device.id).exists()


# --- quiet periods: writes only for the device's own user -------------------


@pytest.mark.django_db
def test_app_owner_cannot_create_quiet_period_on_subscriber_device(tenants):
    device = tenants["device"]
    resp = _client(tenants["owner_a"]).post(
        f"/api/v1/devices/{device.id}/quiet-periods/",
        {
            "name": "Forever",
            "period_type": "RECURRING",
            "recurrence_days": [0, 1, 2, 3, 4, 5, 6],
            "start_time": "00:00:00",
            "end_time": "23:59:00",
            "is_active": True,
        },
        format="json",
    )
    assert resp.status_code == 404
    assert device.quiet_periods.count() == 1


@pytest.mark.django_db
def test_app_owner_cannot_update_or_delete_subscriber_quiet_period(tenants):
    device = tenants["device"]
    qp = tenants["quiet_period"]
    client = _client(tenants["owner_a"])
    url = f"/api/v1/devices/{device.id}/quiet-periods/{qp.id}/"

    resp = client.patch(url, {"is_active": False}, format="json")
    assert resp.status_code == 404
    qp.refresh_from_db()
    assert qp.is_active is True

    resp = client.delete(url)
    assert resp.status_code == 404
    assert DeviceQuietPeriod.objects.filter(id=qp.id).exists()


@pytest.mark.django_db
def test_app_owner_can_still_read_subscriber_quiet_periods(tenants):
    # The console counts/lists quiet periods of every visible device.
    device = tenants["device"]
    qp = tenants["quiet_period"]
    client = _client(tenants["owner_a"])
    resp = client.get(f"/api/v1/devices/{device.id}/quiet-periods/")
    assert resp.status_code == 200
    assert [row["id"] for row in resp.data] == [qp.id]
    resp = client.get(f"/api/v1/devices/{device.id}/quiet-periods/{qp.id}/")
    assert resp.status_code == 200


@pytest.mark.django_db
def test_device_user_can_still_manage_own_quiet_periods(tenants):
    device = tenants["device"]
    qp = tenants["quiet_period"]
    client = _client(tenants["subscriber_b"])
    resp = client.post(
        f"/api/v1/devices/{device.id}/quiet-periods/",
        {
            "name": "Lunch",
            "period_type": "RECURRING",
            "recurrence_days": [0],
            "start_time": "12:00:00",
            "end_time": "13:00:00",
            "is_active": True,
        },
        format="json",
    )
    assert resp.status_code == 201
    url = f"/api/v1/devices/{device.id}/quiet-periods/{qp.id}/"
    assert client.patch(url, {"is_active": False}, format="json").status_code == 200
    assert client.delete(url).status_code == 204


@pytest.mark.django_db
def test_unrelated_user_gets_404_everywhere(tenants):
    device = tenants["device"]
    qp = tenants["quiet_period"]
    stranger = User.objects.create_user(email="stranger@example.com", password=PASSWORD)
    client = _client(stranger)
    assert client.get(f"/api/v1/devices/{device.id}/").status_code == 404
    assert client.get(f"/api/v1/devices/{device.id}/quiet-periods/").status_code == 404
    assert client.get(f"/api/v1/devices/{device.id}/quiet-periods/{qp.id}/").status_code == 404


# --- owner read view: no disclosure of other owners' apps --------------------


@pytest.mark.django_db
def test_owner_device_detail_only_lists_own_applications(tenants):
    device = tenants["device"]
    resp = _client(tenants["owner_a"]).get(f"/api/v1/devices/{device.id}/")
    assert resp.status_code == 200
    assert resp.data["application_ids"] == [tenants["app_a"].id]
    assert resp.data["unlinked_applications"] == []


@pytest.mark.django_db
def test_owner_device_list_only_lists_own_applications(tenants):
    resp = _client(tenants["owner_a"]).get("/api/v1/devices/")
    assert resp.status_code == 200
    rows = {row["id"]: row for row in resp.data}
    row = rows[tenants["device"].id]
    assert row["application_ids"] == [tenants["app_a"].id]
    assert row["unlinked_applications"] == []


@pytest.mark.django_db
def test_owner_sees_own_unlinked_application_only(tenants):
    # Owner C sees its own past link, not owner A's app.
    device = tenants["device"]
    resp = _client(tenants["owner_c"]).get(f"/api/v1/devices/{device.id}/")
    assert resp.status_code == 200
    assert resp.data["application_ids"] == [tenants["app_c"].id]
    assert [u["application_id"] for u in resp.data["unlinked_applications"]] == [
        tenants["app_c_old"].id
    ]


@pytest.mark.django_db
def test_device_user_sees_all_own_links(tenants):
    device = tenants["device"]
    resp = _client(tenants["subscriber_b"]).get(f"/api/v1/devices/{device.id}/")
    assert resp.status_code == 200
    assert sorted(resp.data["application_ids"]) == sorted(
        [tenants["app_a"].id, tenants["app_c"].id]
    )
    assert [u["application_id"] for u in resp.data["unlinked_applications"]] == [
        tenants["app_c_old"].id
    ]


# --- is_mine: lets the console hide actions the API would refuse -------------


@pytest.mark.django_db
def test_is_mine_true_for_device_user_on_detail_and_list(tenants):
    device = tenants["device"]
    client = _client(tenants["subscriber_b"])
    assert client.get(f"/api/v1/devices/{device.id}/").data["is_mine"] is True
    rows = {row["id"]: row for row in client.get("/api/v1/devices/").data}
    assert rows[device.id]["is_mine"] is True


@pytest.mark.django_db
def test_is_mine_false_for_app_owner_on_detail_and_list(tenants):
    device = tenants["device"]
    client = _client(tenants["owner_a"])
    assert client.get(f"/api/v1/devices/{device.id}/").data["is_mine"] is False
    rows = {row["id"]: row for row in client.get("/api/v1/devices/").data}
    assert rows[device.id]["is_mine"] is False


@pytest.mark.django_db
def test_is_mine_is_read_only(tenants):
    device = tenants["device"]
    client = _client(tenants["subscriber_b"])
    resp = client.patch(f"/api/v1/devices/{device.id}/", {"is_mine": False}, format="json")
    assert resp.status_code == 200
    assert client.get(f"/api/v1/devices/{device.id}/").data["is_mine"] is True


@pytest.mark.django_db
def test_device_update_answers_with_full_device_read(tenants):
    device = tenants["device"]
    client = _client(tenants["subscriber_b"])
    for method in (client.patch, client.put):
        resp = method(
            f"/api/v1/devices/{device.id}/",
            {"device_name": "Renamed", "platform": "android", "push_token_status": "active"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["id"] == device.id
        assert resp.data["device_name"] == "Renamed"
        assert resp.data["is_mine"] is True
        assert sorted(resp.data["application_ids"]) == sorted(
            [tenants["app_a"].id, tenants["app_c"].id]
        )
