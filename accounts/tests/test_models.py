from django.test import TestCase

import pytest
from accounts.models import User


@pytest.mark.django_db
def test_user_has_generated_userkey():
    user = User.objects.create_user(
        username="renaud",
        email="renaud@example.com",
        password="secret123"
    )
    assert user.userkey.startswith("usr_")
    assert len(user.userkey) == 16

@pytest.mark.django_db
def test_userkey_is_unique():
    user1 = User.objects.create_user(username="u1", email="u1@website.com", password="1234")
    user2 = User.objects.create_user(username="u2", email="u2@website.com", password="1234")

    assert user1.userkey != user2.userkey