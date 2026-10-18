import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_ready_failure_does_not_leak_database_error(monkeypatch, caplog):
    from django.db import connections
    from django.db.utils import OperationalError

    class BrokenCursor:
        def __enter__(self):
            raise OperationalError("could not connect to server at 10.1.2.3 user=secret_role")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(connections["default"], "cursor", lambda: BrokenCursor())
    response = APIClient().get("/health/ready/")

    assert response.status_code == 503
    body = response.json()
    assert body["detail"] == "database_unavailable"
    assert "10.1.2.3" not in response.content.decode()
    assert "secret_role" not in response.content.decode()
