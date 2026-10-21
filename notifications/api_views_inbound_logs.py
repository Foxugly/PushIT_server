from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import permissions
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from applications.models import Application
from .models import InboundEmailIngestionLog
from .serializers import DetailResponseSerializer, InboundEmailIngestionLogSerializer

INBOUND_EMAIL_LOG_LIMIT = 50


class ApplicationInboundEmailLogListApiView(APIView):
    """Inbound e-mail journal of one application, for its owner."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="apps_inbound_email_logs_list",
        summary="Inbound e-mail journal",
        description=(
            f"Returns the {INBOUND_EMAIL_LOG_LIMIT} most recent inbound e-mails processed for an "
            "application owned by the authenticated user, newest first, as a bare array "
            "(no pagination). A refused mail has `status=rejected` and an `error_message` "
            "starting with a stable reason code (`sender_not_allowed`, `sender_blocked`, "
            "`dmarc_failed`). Mail that matched no application is not listed here."
        ),
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
        responses={
            200: InboundEmailIngestionLogSerializer(many=True),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Application not found"),
        },
    )
    def get(self, request, app_id):
        if not Application.objects.filter(id=app_id, owner=request.user).exists():
            raise NotFound("Application not found.", code="application_not_found")
        logs = InboundEmailIngestionLog.objects.filter(application_id=app_id).order_by("-processed_at", "-id")[
            :INBOUND_EMAIL_LOG_LIMIT
        ]
        return Response(InboundEmailIngestionLogSerializer(logs, many=True).data)
