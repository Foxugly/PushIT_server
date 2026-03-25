import logging

import pytest
from django.test import override_settings
from django.urls import path
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.authentication import AppTokenAuthentication
from applications.models import Application
from applications.permissions import HasAppToken


class ExplodingJwtView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        raise RuntimeError("boom-jwt")


class ExplodingAppTokenView(APIView):
    authentication_classes = [AppTokenAuthentication]
    permission_classes = [HasAppToken]

    def get(self, request):
        return Response({"unexpected": True})

    def post(self, request):
        raise RuntimeError("boom-app-token")


urlpatterns = [
    path("test/explode/jwt/", ExplodingJwtView.as_view()),
    path("test/explode/app-token/", ExplodingAppTokenView.as_view()),
]


@pytest.mark.django_db
@override_settings(ROOT_URLCONF=__name__)
def test_internal_error_response_includes_incident_id_and_logs_user_context(caplog):
    client = APIClient()
    user = User.objects.create_user(
        email="incident@example.com",
        username="incident",
        password="MotDePasseTresSolide123!",
    )
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    with caplog.at_level(logging.ERROR, logger="pushit.api"):
        response = client.get("/test/explode/jwt/")

    assert response.status_code == 500
    assert response.data["code"] == "internal_error"
    assert response.data["detail"] == "Internal server error."
    assert response.data["incident_id"].startswith("inc_")
    assert response["X-Request-ID"]

    record = next(record for record in caplog.records if record.name == "pushit.api")
    assert record.incident_id == response.data["incident_id"]
    assert record.request_id == response["X-Request-ID"]
    assert record.error_code == "internal_error"
    assert record.path == "/test/explode/jwt/"
    assert record.method == "GET"
    assert record.user_id == user.id


@pytest.mark.django_db
@override_settings(ROOT_URLCONF=__name__)
def test_internal_error_logs_application_context_for_app_token_requests(caplog):
    client = APIClient()
    user = User.objects.create_user(
        email="app@example.com",
        username="app-owner",
        password="MotDePasseTresSolide123!",
    )
    application = Application.objects.create(owner=user, name="App")
    raw_token = application.set_new_app_token()
    application.save()

    with caplog.at_level(logging.ERROR, logger="pushit.api"):
        response = client.post(
            "/test/explode/app-token/",
            {},
            format="json",
            HTTP_X_APP_TOKEN=raw_token,
        )

    assert response.status_code == 500
    assert response.data["code"] == "internal_error"
    assert response.data["incident_id"].startswith("inc_")

    record = next(record for record in caplog.records if record.name == "pushit.api")
    assert record.incident_id == response.data["incident_id"]
    assert record.error_code == "internal_error"
    assert record.path == "/test/explode/app-token/"
    assert record.method == "POST"
    assert record.application_id == application.id
