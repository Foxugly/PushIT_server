"""Per-application inbound e-mail sender policy: list validation, matching
rules, and the read/update API used by the console."""

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications import inbound_sender_policy as policy
from applications.models import Application

PWD = "MotDePasseTresSolide123!"


# ------------------------------------------------------------ list validation


def test_entries_are_lowercased_trimmed_and_deduped_in_order():
    assert policy.normalize_entries([" Alice@Example.com ", "@Corp.IO", "alice@example.com", "", "  "]) == [
        "alice@example.com",
        "@corp.io",
    ]


@pytest.mark.parametrize("bad", ["not-an-address", "@", "@nodot", "a@@b.com", "@b.com@c.com", "alice@", "x" * 260])
def test_invalid_entries_are_refused(bad):
    with pytest.raises(ValidationError):
        policy.normalize_entries([bad])


def test_list_is_capped():
    entries = [f"user{i}@example.com" for i in range(policy.MAX_SENDER_LIST_ENTRIES)]
    assert len(policy.normalize_entries(entries)) == policy.MAX_SENDER_LIST_ENTRIES
    with pytest.raises(ValidationError):
        policy.normalize_entries(entries + ["one-more@example.com"])


def test_non_list_is_refused():
    with pytest.raises(ValidationError):
        policy.normalize_entries("alice@example.com")


@pytest.mark.parametrize(
    "sender,entries,expected",
    [
        ("alice@example.com", ["alice@example.com"], True),
        ("ALICE@example.com", ["alice@example.com"], True),
        ("bob@example.com", ["@example.com"], True),
        ("bob@sub.example.com", ["@example.com"], False),  # exact domain only
        ("bob@example.com.evil.io", ["@example.com"], False),
        ("bob@example.com", ["alice@example.com"], False),
        ("bob@example.com", [], False),
        ("garbage", ["@example.com"], False),
    ],
)
def test_sender_matches(sender, entries, expected):
    assert policy.sender_matches(sender, entries) is expected


# ------------------------------------------------------------- decision rules


def _app(**fields):
    owner = User(email="owner@example.com")
    return Application(owner=owner, **fields)


@pytest.mark.parametrize(
    "fields,sender,accepted,reason",
    [
        # owner mode (default)
        ({}, "owner@example.com", True, policy.ACCEPTED_OWNER),
        ({}, "OWNER@example.com", True, policy.ACCEPTED_OWNER),
        ({}, "stranger@example.com", False, policy.REASON_NOT_ALLOWED),
        ({"inbound_sender_allowlist": ["ally@partner.io"]}, "ally@partner.io", True, policy.ACCEPTED_ALLOWLIST),
        ({"inbound_sender_allowlist": ["@partner.io"]}, "anyone@partner.io", True, policy.ACCEPTED_ALLOWLIST),
        # blocklist wins over the allowlist...
        (
            {"inbound_sender_allowlist": ["@partner.io"], "inbound_sender_blocklist": ["bad@partner.io"]},
            "bad@partner.io",
            False,
            policy.REASON_BLOCKLISTED,
        ),
        # ...but never blocks the owner's own address.
        ({"inbound_sender_blocklist": ["@example.com"]}, "owner@example.com", True, policy.ACCEPTED_OWNER),
        # anyone mode
        ({"inbound_sender_mode": "anyone"}, "stranger@else.org", True, policy.ACCEPTED_ANYONE),
        (
            {"inbound_sender_mode": "anyone", "inbound_sender_blocklist": ["@else.org"]},
            "stranger@else.org",
            False,
            policy.REASON_BLOCKLISTED,
        ),
        (
            {"inbound_sender_mode": "anyone", "inbound_sender_blocklist": ["@example.com"]},
            "owner@example.com",
            True,
            policy.ACCEPTED_OWNER,
        ),
    ],
)
def test_policy_decision(fields, sender, accepted, reason):
    decision = policy.evaluate_sender_policy(_app(**fields), sender)
    assert (decision.accepted, decision.reason) == (accepted, reason)


# ------------------------------------------------------------------------ API


@pytest.fixture
def api(db):
    user = User.objects.create_user(email="console@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Console App")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client, app


def test_read_exposes_policy_with_safe_defaults(api):
    client, app = api
    data = client.get(f"/api/v1/apps/{app.id}/").data
    assert data["inbound_sender_mode"] == "owner"
    assert data["inbound_sender_allowlist"] == []
    assert data["inbound_sender_blocklist"] == []
    assert data["inbound_rejection_notify_email"] is True
    assert data["inbound_rejection_notify_push"] is False
    assert data["inbound_rejection_reply_to_sender"] is False

    listed = client.get("/api/v1/apps/").data
    rows = listed["results"] if isinstance(listed, dict) else listed
    assert rows[0]["inbound_sender_mode"] == "owner"


def test_patch_updates_and_normalizes_the_policy(api):
    client, app = api
    response = client.patch(
        f"/api/v1/apps/{app.id}/",
        {
            "inbound_sender_mode": "anyone",
            "inbound_sender_allowlist": ["Ally@Partner.io", "@corp.io", "ally@partner.io"],
            "inbound_sender_blocklist": ["@SPAM.example"],
            "inbound_rejection_notify_email": False,
            "inbound_rejection_notify_push": True,
            "inbound_rejection_reply_to_sender": True,
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["inbound_sender_mode"] == "anyone"
    assert response.data["inbound_sender_allowlist"] == ["ally@partner.io", "@corp.io"]
    assert response.data["inbound_sender_blocklist"] == ["@spam.example"]

    app.refresh_from_db()
    assert app.inbound_sender_mode == "anyone"
    assert app.inbound_sender_allowlist == ["ally@partner.io", "@corp.io"]
    assert app.inbound_sender_blocklist == ["@spam.example"]
    assert app.inbound_rejection_notify_email is False
    assert app.inbound_rejection_notify_push is True
    assert app.inbound_rejection_reply_to_sender is True


def test_patch_leaves_unsent_policy_fields_untouched(api):
    client, app = api
    app.inbound_sender_allowlist = ["ally@partner.io"]
    app.save()
    response = client.patch(f"/api/v1/apps/{app.id}/", {"name": "Renamed"}, format="json")
    assert response.status_code == 200
    app.refresh_from_db()
    assert app.inbound_sender_allowlist == ["ally@partner.io"]


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"inbound_sender_allowlist": ["not-an-address"]}, "inbound_sender_allowlist"),
        ({"inbound_sender_blocklist": ["@nodot"]}, "inbound_sender_blocklist"),
        ({"inbound_sender_allowlist": [f"u{i}@example.com" for i in range(201)]}, "inbound_sender_allowlist"),
        ({"inbound_sender_mode": "everyone"}, "inbound_sender_mode"),
    ],
)
def test_patch_rejects_invalid_policy(api, payload, field):
    client, app = api
    response = client.patch(f"/api/v1/apps/{app.id}/", payload, format="json")
    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert field in response.data["errors"]
    app.refresh_from_db()
    assert app.inbound_sender_mode == "owner"
    assert app.inbound_sender_allowlist == []
