import logging

import pytest

from config.logging_utils import mask_email


@pytest.mark.parametrize(
    "raw, masked",
    [
        ("john@x.com", "j***@x.com"),
        ("J@Example.org", "J***@Example.org"),
        ("", ""),
        (None, ""),
        ("not-an-email", "***"),
        ("@x.com", "***@x.com"),
    ],
)
def test_mask_email(raw, masked):
    assert mask_email(raw) == masked


def test_send_email_logs_masked_recipient(caplog, settings):
    from applications import graph_mail

    settings.GRAPH_CLIENT_ID = ""
    with caplog.at_level(logging.WARNING, logger=graph_mail.logger.name):
        graph_mail.send_email(to="secret.person@example.com", subject="s", body="b")
    record = next(r for r in caplog.records if r.getMessage() == "graph_mail_skipped")
    assert record.to == "s***@example.com"


def test_unknown_address_reply_logs_masked_addresses(caplog, monkeypatch):
    from notifications import inbound_reply

    monkeypatch.setattr(inbound_reply, "send_email", lambda **kw: None)
    monkeypatch.setattr(inbound_reply, "build_unknown_address_reply", lambda s, r: ("subj", "body"))
    with caplog.at_level(logging.INFO, logger=inbound_reply.logger.name):
        inbound_reply.send_unknown_address_reply("alice@example.com", "bob@foxugly.com")
    record = next(r for r in caplog.records if r.getMessage() == "inbound_email_unknown_address_reply_sent")
    assert record.sender == "a***@example.com"
    assert record.recipient == "b***@foxugly.com"
