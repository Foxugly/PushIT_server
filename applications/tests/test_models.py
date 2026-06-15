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
    # Format: app_<name-slug>_<8 hex>, e.g. app_mon_app_3f9a2c1b.
    assert re.fullmatch(r"app_mon_app_[0-9a-f]{8}", app.inbound_email_alias), app.inbound_email_alias
    # Domain is env-configured (SSM in prod), so assert against the setting, not a literal.
    assert app.inbound_email_address == f"{app.inbound_email_alias}@{settings.INBOUND_EMAIL_DOMAIN}"

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
