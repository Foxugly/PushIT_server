import pytest
from django.test import override_settings

from notifications import push


@pytest.fixture
def captured(monkeypatch):
    """Capture the firebase Message passed to messaging.send without sending."""
    messaging = pytest.importorskip("firebase_admin.messaging")
    monkeypatch.setattr(push, "_ensure_fcm_initialized", lambda: None)
    sent = []
    monkeypatch.setattr(messaging, "send", lambda msg: (sent.append(msg), "msg-id")[1])
    return sent


@override_settings(PUSH_DELIVERY_MODE="data-only")
def test_data_only_android_omits_notification_block(captured):
    push._send_fcm("tok", "Title", "Body", {"notification_id": 1}, platform="android")
    msg = captured[-1]
    assert msg.notification is None, "Android data-only must omit the notification block"
    assert msg.data["notification_id"] == "1"


@override_settings(PUSH_DELIVERY_MODE="data-only")
def test_data_only_ios_keeps_notification_block(captured):
    push._send_fcm("tok", "Title", "Body", {}, platform="ios")
    assert captured[-1].notification is not None, "iOS can't render data-only — keep the block"


@override_settings(PUSH_DELIVERY_MODE="hybrid")
def test_hybrid_attaches_notification_block(captured):
    push._send_fcm("tok", "Title", "Body", {}, platform="android")
    assert captured[-1].notification is not None, "hybrid always attaches the notification block"


@override_settings(PUSH_DELIVERY_MODE="hybrid")
def test_android_data_only_option_omits_notification_block_even_in_hybrid(captured):
    push._send_fcm(
        "tok", "Title", "Body", {"type": "inbound_email_rejected"}, platform="android", android_data_only=True
    )
    msg = captured[-1]
    assert msg.notification is None, "android_data_only must win over hybrid on Android"
    assert msg.android.priority == "high", "data-only needs high priority to be delivered promptly"
    assert msg.data["type"] == "inbound_email_rejected"


@override_settings(PUSH_DELIVERY_MODE="hybrid")
def test_android_data_only_option_keeps_notification_block_on_ios(captured):
    push._send_fcm("tok", "Title", "Body", {}, platform="ios", android_data_only=True)
    assert captured[-1].notification is not None, "APNs needs the alert block"


def test_send_push_to_device_forwards_android_data_only(monkeypatch, settings):
    settings.FCM_SERVICE_ACCOUNT_PATH = "/fake.json"
    settings.PUSHIT_FORCE_MOCK_PUSH = False
    calls = []
    monkeypatch.setattr(push, "_send_fcm", lambda *a: (calls.append(a), "id")[1])
    push.send_push_to_device("tok", "T", "M", {}, platform="android", android_data_only=True)
    push.send_push_to_device("tok", "T", "M", {}, platform="android")
    assert calls[0][-1] is True
    assert calls[1][-1] is False
