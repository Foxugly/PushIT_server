import re
from io import StringIO

import pytest
from django.core.management import call_command

from accounts.models import User
from applications.models import Application

PWD = "MotDePasseTresSolide123!"


@pytest.mark.django_db
def test_regenerate_migrates_legacy_alias_to_new_format():
    user = User.objects.create_user(email="u@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Mon App")
    # Force a legacy-format alias (the pre-app_ scheme), bypassing save().
    Application.objects.filter(id=app.id).update(inbound_email_alias="mon-app")

    out = StringIO()
    call_command("regenerate_inbound_aliases", stdout=out)

    app.refresh_from_db()
    assert re.fullmatch(r"app_mon_app_[0-9a-f]{6}", app.inbound_email_alias), app.inbound_email_alias
    assert "mon-app ->" in out.getvalue()


@pytest.mark.django_db
def test_regenerate_skips_already_migrated_apps():
    user = User.objects.create_user(email="u2@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Already New")  # generates new format
    before = app.inbound_email_alias
    assert before.startswith("app_")

    out = StringIO()
    call_command("regenerate_inbound_aliases", stdout=out)

    app.refresh_from_db()
    assert app.inbound_email_alias == before
    assert "No legacy aliases" in out.getvalue()


@pytest.mark.django_db
def test_regenerate_dry_run_changes_nothing():
    user = User.objects.create_user(email="u3@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Dry")
    Application.objects.filter(id=app.id).update(inbound_email_alias="dry")

    out = StringIO()
    call_command("regenerate_inbound_aliases", "--dry-run", stdout=out)

    app.refresh_from_db()
    assert app.inbound_email_alias == "dry", "dry-run must not change the alias"
    assert "dry -> app_dry_" in out.getvalue()
