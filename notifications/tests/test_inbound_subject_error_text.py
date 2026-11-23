"""CodeQL py/stack-trace-exposure: the inbound serializer's error list must
carry a fixed message for a bad [SEND_AT:...] marker, never str(exc)."""

from unittest.mock import patch

import pytest

from notifications.inbound_email import INVALID_SEND_AT_MESSAGE
from notifications.serializers import NotificationInboundEmailSerializer


@pytest.mark.django_db
def test_bad_send_at_marker_reports_the_fixed_message():
    serializer = NotificationInboundEmailSerializer(
        data={
            "sender": "someone@example.com",
            "recipient": "nobody@example.com",
            "subject": "Hello [SEND_AT: not-a-date]",
            "text": "body",
        }
    )
    assert not serializer.is_valid()
    assert INVALID_SEND_AT_MESSAGE in serializer.errors["subject"]


@pytest.mark.django_db
def test_parser_exception_text_is_not_reflected():
    secret = "internal parser detail"
    with patch(
        "notifications.serializers.extract_subject_schedule",
        side_effect=ValueError(secret),
    ):
        serializer = NotificationInboundEmailSerializer(
            data={
                "sender": "someone@example.com",
                "recipient": "nobody@example.com",
                "subject": "Hello",
                "text": "body",
            }
        )
        assert not serializer.is_valid()

    assert secret not in str(serializer.errors)
    assert serializer.errors["subject"][0] == INVALID_SEND_AT_MESSAGE
