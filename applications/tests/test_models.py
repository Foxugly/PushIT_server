from django.test import TestCase
import pytest
from accounts.models import User
from applications.models import Application


@pytest.mark.django_db
def test_application_has_generated_app_token():
    user = User.objects.create_user(
        username="renaud",
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
    assert app.inbound_email_alias == "mon-app"
    assert app.inbound_email_address == "mon-app@pushit.com"

@pytest.mark.django_db
def test_app_token_is_unique():
    user = User.objects.create_user(username="u1", password="1234")

    app1 = Application.objects.create(owner=user, name="App1")
    app2 = Application.objects.create(owner=user, name="App2")

    assert app1.app_token_hash != app2.app_token_hash
    assert app1.inbound_email_alias != app2.inbound_email_alias


@pytest.mark.django_db
def test_inbound_email_alias_remains_stable_when_regenerating_app_token():
    user = User.objects.create_user(username="u2", password="1234")
    app = Application.objects.create(owner=user, name="App")
    original_alias = app.inbound_email_alias

    app.set_new_app_token()
    app.save()
    app.refresh_from_db()

    assert app.inbound_email_alias == original_alias
