import pytest
from datetime import timedelta

from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from accounts.models import User
from notifications.tasks import flush_expired_tokens_task


@pytest.mark.django_db
def test_flush_expired_tokens_deletes_expired_keeps_fresh():
    user = User.objects.create_user(
        email="tok@example.com", password="MotDePasseTresSolide123!"
    )
    now = timezone.now()

    expired = OutstandingToken.objects.create(
        user=user,
        jti="expired-jti",
        token="expired-token",
        created_at=now - timedelta(days=400),
        expires_at=now - timedelta(days=1),
    )
    fresh = OutstandingToken.objects.create(
        user=user,
        jti="fresh-jti",
        token="fresh-token",
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    result = flush_expired_tokens_task()

    remaining = set(OutstandingToken.objects.values_list("jti", flat=True))
    assert remaining == {"fresh-jti"}
    assert result["outstanding_deleted"] == 1
    assert not OutstandingToken.objects.filter(pk=expired.pk).exists()
    assert OutstandingToken.objects.filter(pk=fresh.pk).exists()


@pytest.mark.django_db
def test_flush_expired_tokens_deletes_blacklisted_first():
    """A blacklisted row points at an outstanding token via FK; the task must
    delete the blacklist row before its outstanding token to respect the FK."""
    user = User.objects.create_user(
        email="bl@example.com", password="MotDePasseTresSolide123!"
    )
    now = timezone.now()

    expired = OutstandingToken.objects.create(
        user=user,
        jti="bl-jti",
        token="bl-token",
        created_at=now - timedelta(days=400),
        expires_at=now - timedelta(days=1),
    )
    BlacklistedToken.objects.create(token=expired)

    result = flush_expired_tokens_task()

    assert result["blacklisted_deleted"] == 1
    assert result["outstanding_deleted"] == 1
    assert not BlacklistedToken.objects.exists()
    assert not OutstandingToken.objects.exists()
