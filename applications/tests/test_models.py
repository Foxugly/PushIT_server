import re

from django.conf import settings
from django.test import TestCase
import pytest
from accounts.models import User
from applications.models import Application


@pytest.mark.django_db
def test_application_has_generated_app_token():
    user = User.objects.create_user(
        email="renaud@example.com",
        password="secret123"
    )

    app = Application.objects.create(
        owner=user,
        name="Mon App"
    )
    assert app.app_token_prefix.startswith("apt_")
    assert len(app.app_token_prefix) > 4
    assert len(app.app_token_hash) == 64
    # Format: <name-slug>-<8 hex>.pushit, e.g. mon-app-3f9a2c1b.pushit.
    assert re.fullmatch(r"mon-app-[0-9a-f]{8}\.pushit", app.inbound_email_alias), app.inbound_email_alias
    assert app.inbound_email_suffix == app.inbound_email_alias.split("-")[-1].removesuffix(".pushit")
    # Domain is env-configured (SSM in prod), so assert against the setting, not a literal.
    assert app.inbound_email_address == f"{app.inbound_email_alias}@{settings.INBOUND_EMAIL_DOMAIN}"

@pytest.mark.django_db
def test_inbound_alias_suffix_is_unique_across_apps():
    from unittest.mock import patch

    user = User.objects.create_user(email="sfx@example.com", password="secret123")
    # First app's suffix is forced to "deadbeef" (stored, DB-unique).
    app1 = Application.objects.create(owner=user, name="A")
    Application.objects.filter(id=app1.id).update(
        inbound_email_alias="app_a_deadbeef", inbound_email_suffix="deadbeef"
    )

    # Second app: generation first proposes the SAME suffix (collision → the DB
    # UNIQUE constraint raises IntegrityError), then a fresh one — save() must
    # retry and keep the unique one.
    with patch.object(
        Application,
        "generate_inbound_email_alias",
        side_effect=["app_b_deadbeef", "app_b_cafe1234"],
    ):
        app2 = Application.objects.create(owner=user, name="B")

    assert app2.inbound_email_alias == "app_b_cafe1234"
    s1 = "app_a_deadbeef".rsplit("_", 1)[-1]
    s2 = app2.inbound_email_alias.rsplit("_", 1)[-1]
    assert s1 != s2, "suffixes must be globally unique"


@pytest.mark.django_db
def test_app_token_is_unique():
    user = User.objects.create_user(email="u1@example.com", password="1234")

    app1 = Application.objects.create(owner=user, name="App1")
    app2 = Application.objects.create(owner=user, name="App2")

    assert app1.app_token_hash != app2.app_token_hash
    assert app1.inbound_email_alias != app2.inbound_email_alias


@pytest.mark.django_db
def test_inbound_email_alias_remains_stable_when_regenerating_app_token():
    user = User.objects.create_user(email="u2@example.com", password="1234")
    app = Application.objects.create(owner=user, name="App")
    original_alias = app.inbound_email_alias

    app.set_new_app_token()
    app.save()
    app.refresh_from_db()

    assert app.inbound_email_alias == original_alias


def test_suffix_of_handles_both_alias_formats():
    assert Application._suffix_of("mon-app-3f9a2c1b.pushit") == "3f9a2c1b"
    assert Application._suffix_of("MON-APP-3F9A2C1B.PUSHIT") == "3f9a2c1b"
    assert Application._suffix_of("app_mon_app_3f9a2c1b") == "3f9a2c1b"


@pytest.mark.django_db
def test_inbound_alias_local_part_fits_rfc_limit_for_long_names():
    user = User.objects.create_user(email="long@example.com", password="secret123")
    app = Application.objects.create(owner=user, name=("Une application au nom vraiment long " * 4)[:120])
    assert len(app.inbound_email_alias) <= 64
    assert re.fullmatch(r"[a-z0-9-]+-[0-9a-f]{8}\.pushit", app.inbound_email_alias)


@pytest.mark.django_db
def test_inbound_alias_falls_back_when_name_has_no_slug():
    user = User.objects.create_user(email="noslug@example.com", password="secret123")
    app = Application.objects.create(owner=user, name="!!!")
    assert re.fullmatch(r"app-[0-9a-f]{8}\.pushit", app.inbound_email_alias)


@pytest.mark.django_db
def test_inbound_alias_never_equals_the_mailbox_address(settings):
    from unittest.mock import patch

    settings.INBOUND_EMAIL_DOMAIN = "pushit.com"
    settings.GRAPH_MAILBOX_USER_ID = "box-00000000.pushit@pushit.com"
    user = User.objects.create_user(email="mbx@example.com", password="secret123")
    with patch.object(
        Application,
        "generate_inbound_email_alias",
        side_effect=["box-00000000.pushit", "box-11111111.pushit"],
    ):
        app = Application.objects.create(owner=user, name="Box")
    assert app.inbound_email_alias == "box-11111111.pushit"
