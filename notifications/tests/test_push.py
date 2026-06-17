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
