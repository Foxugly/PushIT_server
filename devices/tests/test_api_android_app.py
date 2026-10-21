import json

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


# ------------------------------------------------------------ version endpoint

VERSION_URL = "/api/v1/devices/android-app/version/"


def _publish(tmp_path, settings, meta, apk_bytes=b"PK-apk-content"):
    apk = tmp_path / "pushit.apk"
    apk.write_bytes(apk_bytes)
    sidecar = tmp_path / "pushit.json"
    if meta is not None:
        sidecar.write_text(meta if isinstance(meta, str) else json.dumps(meta), encoding="utf-8")
    settings.ANDROID_APK_PATH = apk
    settings.ANDROID_APK_META_PATH = sidecar
    return apk


def test_default_meta_path_sits_next_to_the_apk():
    from django.conf import settings as real_settings

    assert real_settings.ANDROID_APK_META_PATH == real_settings.ANDROID_APK_PATH.with_name("pushit.json")


@pytest.mark.django_db
def test_version_anonymous_is_refused(tmp_path, settings):
    _publish(tmp_path, settings, {"version_code": 7, "version_name": "1.2.3"})

    assert _client().get(VERSION_URL).status_code == 401


def test_version_returns_code_name_and_size(user, tmp_path, settings):
    _publish(tmp_path, settings, {"version_code": 7, "version_name": "1.2.3"}, apk_bytes=b"x" * 1234)

    response = _client(user).get(VERSION_URL)

    assert response.status_code == 200
    assert response.json() == {"version_code": 7, "version_name": "1.2.3", "size": 1234}


@pytest.mark.parametrize(
    "meta",
    [
        None,  # sidecar missing
        "not json",
        "[]",
        {"version_name": "1.2.3"},
        {"version_code": 7},
        {"version_code": "7", "version_name": "1.2.3"},
        {"version_code": True, "version_name": "1.2.3"},
        {"version_code": 0, "version_name": "1.2.3"},
        {"version_code": 7, "version_name": ""},
        {"version_code": 7, "version_name": 123},
        "{" + " " * 5000 + "}",
    ],
)
def test_version_missing_or_invalid_sidecar_is_a_404(user, tmp_path, settings, meta):
    _publish(tmp_path, settings, meta)

    response = _client(user).get(VERSION_URL)

    assert response.status_code == 404
    assert response.data["code"] == "android_app_not_found"


def test_version_without_apk_is_a_404(user, tmp_path, settings):
    apk = _publish(tmp_path, settings, {"version_code": 7, "version_name": "1.2.3"})
    apk.unlink()

    response = _client(user).get(VERSION_URL)

    assert response.status_code == 404
    assert response.data["code"] == "android_app_not_found"
