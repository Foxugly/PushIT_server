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

@pytest.mark.django_db
def test_app_token_is_unique():
    user = User.objects.create_user(username="u1", password="1234")

    app1 = Application.objects.create(owner=user, name="App1")
    app2 = Application.objects.create(owner=user, name="App2")

    assert app1.app_token_hash != app2.app_token_hash