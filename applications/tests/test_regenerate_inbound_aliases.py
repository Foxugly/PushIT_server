import re
from io import StringIO

import pytest
from django.core.management import call_command

from accounts.models import User
from applications.models import Application

PWD = "MotDePasseTresSolide123!"
NEW_FORMAT = r"mon-app-[0-9a-f]{8}\.pushit"


@pytest.mark.django_db
def test_regenerate_migrates_legacy_alias_to_new_format():
    user = User.objects.create_user(email="u@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Mon App")
    # Force a legacy-format alias (the pre-app_ scheme), bypassing save().
    Application.objects.filter(id=app.id).update(inbound_email_alias="mon-app")

    out = StringIO()
    call_command("regenerate_inbound_aliases", stdout=out)

    app.refresh_from_db()
    assert re.fullmatch(NEW_FORMAT, app.inbound_email_alias), app.inbound_email_alias
    assert app.inbound_email_suffix == app.inbound_email_alias[-15:-7]
    assert "mon-app ->" in out.getvalue()


@pytest.mark.django_db
def test_regenerate_skips_already_migrated_apps():
    user = User.objects.create_user(email="u2@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Already New")  # generates new format
    before = app.inbound_email_alias
    assert before.endswith(".pushit")

    out = StringIO()
    call_command("regenerate_inbound_aliases", "--include-app-prefix", stdout=out)

    app.refresh_from_db()
    assert app.inbound_email_alias == before
    assert "No legacy aliases" in out.getvalue()


@pytest.mark.django_db
def test_regenerate_keeps_app_prefix_aliases_by_default():
    user = User.objects.create_user(email="u4@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Mon App")
    Application.objects.filter(id=app.id).update(
        inbound_email_alias="app_mon_app_deadbeef", inbound_email_suffix="deadbeef"
    )

    out = StringIO()
    call_command("regenerate_inbound_aliases", stdout=out)

    app.refresh_from_db()
    assert app.inbound_email_alias == "app_mon_app_deadbeef"
    assert "No legacy aliases" in out.getvalue()


@pytest.mark.django_db
def test_regenerate_include_app_prefix_migrates_app_prefix_aliases():
    user = User.objects.create_user(email="u5@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Mon App")
    Application.objects.filter(id=app.id).update(
        inbound_email_alias="app_mon_app_deadbeef", inbound_email_suffix="deadbeef"
    )

    out = StringIO()
    call_command("regenerate_inbound_aliases", "--include-app-prefix", stdout=out)

    app.refresh_from_db()
    assert re.fullmatch(NEW_FORMAT, app.inbound_email_alias), app.inbound_email_alias
    assert "app_mon_app_deadbeef ->" in out.getvalue()


@pytest.mark.django_db
def test_regenerate_dry_run_changes_nothing():
    user = User.objects.create_user(email="u3@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Dry")
    Application.objects.filter(id=app.id).update(inbound_email_alias="dry")

    out = StringIO()
    call_command("regenerate_inbound_aliases", "--dry-run", stdout=out)

    app.refresh_from_db()
    assert app.inbound_email_alias == "dry", "dry-run must not change the alias"
    assert re.search(r"dry -> dry-[0-9a-f]{8}\.pushit", out.getvalue())
