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
    raw_token = app.set_new_app_token()
    app.save()

    assert app.check_app_token(raw_token) is True
    assert app.check_app_token("apt_invalid_token") is False