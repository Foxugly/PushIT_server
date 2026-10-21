import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User

URL = "/api/v1/devices/android-app/"


def _client(user=None):
    client = APIClient()
    if user is not None:
        access = str(RefreshToken.for_user(user).access_token)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


@pytest.fixture
def user(db):
    return User.objects.create_user(email="apk@example.com", password="MotDePasseTresSolide123!")


@pytest.mark.django_db
def test_anonymous_is_refused(tmp_path, settings):
    apk = tmp_path / "pushit.apk"
    apk.write_bytes(b"PK-apk")
    settings.ANDROID_APK_PATH = apk

    response = _client().get(URL)

    assert response.status_code == 401


def test_signed_in_user_gets_the_apk(user, tmp_path, settings):
    apk = tmp_path / "pushit.apk"
    apk.write_bytes(b"PK-apk-content")
    settings.ANDROID_APK_PATH = apk

    response = _client(user).get(URL)

    assert response.status_code == 200
    assert response["Content-Type"] == "application/vnd.android.package-archive"
    assert 'filename="pushit.apk"' in response["Content-Disposition"]
    assert b"".join(response.streaming_content) == b"PK-apk-content"


def test_missing_apk_is_a_404(user, tmp_path, settings):
    settings.ANDROID_APK_PATH = tmp_path / "absent.apk"

    response = _client(user).get(URL)

    assert response.status_code == 404
    assert response.data["code"] == "android_app_not_found"
