import hmac

from django.conf import settings
from django.db import connections
from django.db.utils import Error as DatabaseError
from django.http import HttpResponse, JsonResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework import serializers
from rest_framework.views import APIView

from config.metrics import PROMETHEUS_CONTENT_TYPE, render_metrics


class HealthStatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    service = serializers.CharField()
    check = serializers.CharField()


class HealthErrorSerializer(serializers.Serializer):
    status = serializers.CharField()
    service = serializers.CharField()
    check = serializers.CharField()
    detail = serializers.CharField()


@extend_schema_view(
    get=extend_schema(
        summary="Liveness probe",
        description="Checks that the HTTP process is alive. Public endpoint used by infrastructure.",
        tags=["Health"],
        auth=[],
        responses={
            200: OpenApiResponse(
                response=HealthStatusSerializer,
                description="Service alive",
                examples=[
                    OpenApiExample(
                        "Liveness OK",
                        value={"status": "ok", "service": "pushit", "check": "live"},
                        response_only=True,
                    )
                ],
            )
        },
    )
)
class HealthLiveApiView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return JsonResponse({"status": "ok", "service": "pushit", "check": "live"})


@extend_schema_view(
    get=extend_schema(
        summary="Readiness probe",
        description="Checks that the service is ready to serve traffic, including database access.",
        tags=["Health"],
        auth=[],
        responses={
            200: OpenApiResponse(
                response=HealthStatusSerializer,
                description="Service ready",
                examples=[
                    OpenApiExample(
                        "Readiness OK",
                        value={"status": "ok", "service": "pushit", "check": "ready"},
                        response_only=True,
                    )
                ],
            ),
            503: OpenApiResponse(
                response=HealthErrorSerializer,
                description="Dependency unavailable",
                examples=[
                    OpenApiExample(
                        "Database unavailable",
                        value={
                            "status": "error",
                            "service": "pushit",
                            "check": "ready",
                            "detail": "database_unavailable: database is locked",
                        },
                        response_only=True,
                    )
                ],
            ),
        },
    )
)
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


@extend_schema_view(
    get=extend_schema(
        summary="Prometheus metrics",
        description="Exposes Prometheus metrics. Can be protected via the `X-Metrics-Token` header.",
        tags=["Health"],
        auth=[],
        parameters=[
            OpenApiParameter(
                name="X-Metrics-Token",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=False,
                description="Optional token to protect access to `/health/metrics/`.",
            )
        ],
        responses={
            (200, "text/plain"): OpenApiResponse(
                response=OpenApiTypes.STR,
                description="Prometheus text payload",
                examples=[
                    OpenApiExample(
                        "Metrics sample",
                        value='# HELP pushit_process_uptime_seconds Process uptime in seconds\n# TYPE pushit_process_uptime_seconds gauge\npushit_process_uptime_seconds 12.345',
                        response_only=True,
                    )
                ],
            ),
            403: OpenApiResponse(
                response=HealthErrorSerializer,
                description="Invalid metrics token",
                examples=[
                    OpenApiExample(
                        "Metrics forbidden",
                        value={
                            "status": "error",
                            "service": "pushit",
                            "check": "metrics",
                            "detail": "metrics_token_invalid",
                        },
                        response_only=True,
                    )
                ],
            ),
        },
    )
)
class MetricsApiView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        expected_token = settings.METRICS_AUTH_TOKEN
        if expected_token:
            provided_token = request.headers.get("X-Metrics-Token", "").strip()
            if not hmac.compare_digest(provided_token, expected_token):
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
            content_type=PROMETHEUS_CONTENT_TYPE,
        )
