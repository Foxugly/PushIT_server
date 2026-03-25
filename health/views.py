from django.conf import settings
from django.db import connections
from django.db.utils import Error as DatabaseError
from django.http import HttpResponse, JsonResponse
from rest_framework import status
from rest_framework.views import APIView

from config.metrics import render_metrics


class HealthLiveApiView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return JsonResponse({"status": "ok", "service": "pushit", "check": "live"})


class HealthReadyApiView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except DatabaseError as exc:
            return JsonResponse(
                {
                    "status": "error",
                    "service": "pushit",
                    "check": "ready",
                    "detail": f"database_unavailable: {exc}",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return JsonResponse({"status": "ok", "service": "pushit", "check": "ready"})


class MetricsApiView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        expected_token = settings.METRICS_AUTH_TOKEN
        if expected_token:
            provided_token = request.headers.get("X-Metrics-Token", "").strip()
            if provided_token != expected_token:
                return JsonResponse(
                    {
                        "status": "error",
                        "service": "pushit",
                        "check": "metrics",
                        "detail": "metrics_token_invalid",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        return HttpResponse(
            render_metrics(),
            content_type="text/plain; version=0.0.4; charset=utf-8",
        )
