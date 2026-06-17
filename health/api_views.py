"""Authenticated admin backend-status endpoint.

Unlike the public liveness/readiness probes in ``health.views``, this view is
gated behind ``IsAdminUser`` (``is_staff``) and aggregates a richer set of
checks + cheap metrics so the SPA's admin area can render an at-a-glance health
panel. Every check is independently wrapped in try/except so a single failing
dependency degrades the response to ``status="degraded"`` rather than 500-ing
the whole endpoint.
"""

from datetime import timedelta

from django.conf import settings
from django.db import connections
from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from applications.models import Application
from devices.models import Device
from exchange.integration import is_configured as exchange_is_configured
from notifications.models import Notification, NotificationStatus


class AdminStatusCheckSerializer(serializers.Serializer):
    status = serializers.CharField()
    detail = serializers.CharField(required=False)
    configured = serializers.BooleanField(required=False)


class AdminStatusResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    checks = serializers.DictField(child=AdminStatusCheckSerializer())
    metrics = serializers.DictField()


def _check_database() -> dict:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"status": "ok", "detail": "reachable"}
    except Exception as exc:  # noqa: BLE001 — never let a check 500 the endpoint
        return {"status": "error", "detail": str(exc)}


def _check_celery_broker() -> dict:
    try:
        from config.celery import app

        app.connection().ensure_connection(max_retries=1, timeout=2)
        return {"status": "ok", "detail": "reachable"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}


def _check_celery_workers() -> dict:
    try:
        from config.celery import app

        replies = app.control.ping(timeout=2)
        if replies:
            return {"status": "ok", "detail": f"{len(replies)} worker(s) responded"}
        # No worker reply is not necessarily an error — beat/worker may be busy.
        return {"status": "degraded", "detail": "no workers responded"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}


def _check_exchange() -> dict:
    try:
        return {"status": "ok", "configured": bool(exchange_is_configured())}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}


def _collect_metrics() -> dict:
    metrics: dict = {}

    try:
        metrics["applications"] = {
            "total": Application.objects.count(),
            "active": Application.objects.filter(is_active=True).count(),
        }
    except Exception as exc:  # noqa: BLE001
        metrics["applications"] = {"error": str(exc)}

    try:
        metrics["devices"] = {"total": Device.objects.count()}
    except Exception as exc:  # noqa: BLE001
        metrics["devices"] = {"error": str(exc)}

    try:
        counts = {
            row["status"]: row["count"]
            for row in Notification.objects.values("status").annotate(count=Count("id"))
        }
        metrics["notifications"] = {
            status.value: counts.get(status.value, 0)
            for status in NotificationStatus
        }
    except Exception as exc:  # noqa: BLE001
        metrics["notifications"] = {"error": str(exc)}

    try:
        stuck_minutes = getattr(settings, "NOTIFICATION_PROCESSING_STUCK_MINUTES", 15)
        cutoff = timezone.now() - timedelta(minutes=stuck_minutes)
        metrics["processing_stuck"] = Notification.objects.filter(
            status=NotificationStatus.PROCESSING,
            processing_started_at__isnull=False,
            processing_started_at__lt=cutoff,
        ).count()
    except Exception as exc:  # noqa: BLE001
        metrics["processing_stuck"] = {"error": str(exc)}

    return metrics


@extend_schema(
    summary="Admin backend status",
    description=(
        "Aggregated backend health for the admin area: database, Celery broker/"
        "workers and Exchange configuration checks, plus cheap operational "
        "metrics. Requires a staff (admin) user. Overall `status` is `degraded` "
        "if any check reports `error`."
    ),
    tags=["Admin"],
    auth=[{"BearerAuth": []}],
    responses={
        200: OpenApiResponse(
            response=AdminStatusResponseSerializer,
            description="Backend status",
            examples=[
                OpenApiExample(
                    "Status OK",
                    value={
                        "status": "ok",
                        "checks": {
                            "database": {"status": "ok", "detail": "reachable"},
                            "celery_broker": {"status": "ok", "detail": "reachable"},
                            "celery_workers": {"status": "ok", "detail": "1 worker(s) responded"},
                            "exchange": {"status": "ok", "configured": False},
                        },
                        "metrics": {
                            "applications": {"total": 3, "active": 2},
                            "devices": {"total": 12},
                            "notifications": {
                                "draft": 0, "scheduled": 1, "queued": 0,
                                "processing": 0, "sent": 40, "failed": 1,
                                "partial": 0, "no_target": 0,
                            },
                            "processing_stuck": 0,
                        },
                    },
                    response_only=True,
                )
            ],
        ),
    },
)
class AdminStatusApiView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        checks = {
            "database": _check_database(),
            "celery_broker": _check_celery_broker(),
            "celery_workers": _check_celery_workers(),
            "exchange": _check_exchange(),
        }
        overall = "degraded" if any(c.get("status") == "error" for c in checks.values()) else "ok"
        return Response(
            {
                "status": overall,
                "checks": checks,
                "metrics": _collect_metrics(),
            }
        )
