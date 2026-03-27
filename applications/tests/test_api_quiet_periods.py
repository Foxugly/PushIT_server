from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application, QuietPeriodType


@pytest.mark.django_db
def test_create_list_update_and_delete_quiet_period():
    client = APIClient()
    user = User.objects.create_user(
        email="quiet@example.com",
        username="quiet",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Quiet App")
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    start_at = timezone.now() + timedelta(hours=1)
    end_at = start_at + timedelta(hours=2)

    create_response = client.post(
        f"/api/v1/apps/{app.id}/quiet-periods/",
        {
            "name": "Nuit",
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "is_active": True,
        },
        format="json",
    )

    assert create_response.status_code == 201
    quiet_period_id = create_response.data["id"]
    assert create_response.data["name"] == "Nuit"

    list_response = client.get(f"/api/v1/apps/{app.id}/quiet-periods/")
    assert list_response.status_code == 200
    assert len(list_response.data) == 1

    patch_response = client.patch(
        f"/api/v1/apps/{app.id}/quiet-periods/{quiet_period_id}/",
        {"name": "Silence radio"},
        format="json",
    )
    assert patch_response.status_code == 200
    assert patch_response.data["name"] == "Silence radio"

    delete_response = client.delete(f"/api/v1/apps/{app.id}/quiet-periods/{quiet_period_id}/")
    assert delete_response.status_code == 204

    list_response = client.get(f"/api/v1/apps/{app.id}/quiet-periods/")
    assert list_response.status_code == 200
    assert list_response.data == []


@pytest.mark.django_db
def test_quiet_period_validation_rejects_end_before_start():
    client = APIClient()
    user = User.objects.create_user(
        email="quiet2@example.com",
        username="quiet2",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Quiet App")
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    start_at = timezone.now() + timedelta(hours=2)
    end_at = start_at - timedelta(minutes=15)

    response = client.post(
        f"/api/v1/apps/{app.id}/quiet-periods/",
        {
            "name": "Invalide",
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "is_active": True,
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert "end_at" in response.data["errors"]


@pytest.mark.django_db
def test_create_and_update_recurring_quiet_period():
    client = APIClient()
    user = User.objects.create_user(
        email="quiet3@example.com",
        username="quiet3",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Quiet App")
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    create_response = client.post(
        f"/api/v1/apps/{app.id}/quiet-periods/",
        {
            "name": "Nuit ouvrable",
            "period_type": QuietPeriodType.RECURRING,
            "recurrence_days": [0, 1, 2, 3, 4],
            "start_time": "22:00:00",
            "end_time": "08:00:00",
            "is_active": True,
        },
        format="json",
    )

    assert create_response.status_code == 201
    assert create_response.data["period_type"] == QuietPeriodType.RECURRING
    assert create_response.data["recurrence_days"] == [0, 1, 2, 3, 4]
    assert create_response.data["start_at"] is None
    assert create_response.data["end_at"] is None

    quiet_period_id = create_response.data["id"]
    patch_response = client.patch(
        f"/api/v1/apps/{app.id}/quiet-periods/{quiet_period_id}/",
        {
            "recurrence_days": [0, 1, 2, 3, 4, 5],
        },
        format="json",
    )

    assert patch_response.status_code == 200
    assert patch_response.data["recurrence_days"] == [0, 1, 2, 3, 4, 5]


@pytest.mark.django_db
def test_recurring_quiet_period_validation_requires_days_and_times():
    client = APIClient()
    user = User.objects.create_user(
        email="quiet4@example.com",
        username="quiet4",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Quiet App")
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(
        f"/api/v1/apps/{app.id}/quiet-periods/",
        {
            "name": "Recurrence incomplete",
            "period_type": QuietPeriodType.RECURRING,
            "is_active": True,
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert "recurrence_days" in response.data["errors"]
    assert "start_time" in response.data["errors"]
    assert "end_time" in response.data["errors"]
