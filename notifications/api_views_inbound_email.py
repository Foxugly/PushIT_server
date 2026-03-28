from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import serializers
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import error_response
from .creation import create_notification_with_optional_idempotency
from .inbound_journal import record_inbound_email_ingestion
from .models import InboundEmailIngestionStatus, InboundEmailSource
from .serializers import (
    DetailResponseSerializer,
    NotificationInboundEmailSerializer,
    NotificationInboundEmailValidationErrorResponseSerializer,
    NotificationReadSerializer,
)
from .utils import compute_request_fingerprint


@extend_schema_view(
    post=extend_schema(
        summary="Creer une notification a partir d'un email entrant",
        description=(
            "Transforme un email entrant en notification. Le prefixe de l'adresse "
            "destinataire avant `@` doit correspondre au `inbound_email_alias` stable d'une "
            "application connue. Le sujet devient le titre et le texte brut devient "
            "le message. Un marqueur optionnel `[SEND_AT:2026-03-27T20:00:00+01:00]` "
            "dans le sujet permet de planifier l'envoi."
        ),
        tags=["Notifications"],
        auth=[],
        parameters=[
            OpenApiParameter(
                name="X-Inbound-Email-Secret",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description="Secret partage pour autoriser le webhook email entrant.",
            )
        ],
        request=NotificationInboundEmailSerializer,
        examples=[
            OpenApiExample(
                "Immediate inbound email",
                value={
                    "sender": "owner@pushit.com",
                    "recipient": "apt_fc4471fe12345678@pushit.com",
                    "subject": "Alerte production",
                    "text": "Le batch est termine.",
                    "message_id": "mail-001@example.com",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Scheduled inbound email",
                value={
                    "sender": "owner@pushit.com",
                    "recipient": "apt_fc4471fe12345678@pushit.com",
                    "subject": "Maintenance [SEND_AT:2026-03-27T20:00:00+01:00]",
                    "text": "Maintenance ce soir.",
                    "message_id": "mail-002@example.com",
                },
                request_only=True,
            ),
        ],
        responses={
            200: OpenApiResponse(response=NotificationReadSerializer, description="Notification existante retournee"),
            201: OpenApiResponse(response=NotificationReadSerializer, description="Notification creee"),
            400: OpenApiResponse(
                response=NotificationInboundEmailValidationErrorResponseSerializer,
                description="Donnees invalides",
            ),
            403: OpenApiResponse(response=DetailResponseSerializer, description="Secret invalide ou manquant"),
            409: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Message id deja utilise avec un contenu different",
            ),
        },
    )
)
class NotificationCreateFromInboundEmailApiView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        provided_secret = request.headers.get("X-Inbound-Email-Secret", "").strip()
        expected_secret = settings.INBOUND_EMAIL_SECRET.strip()
        if not provided_secret or provided_secret != expected_secret:
            return error_response(
                code="inbound_email_forbidden",
                detail="Secret inbound email invalide ou manquant.",
                http_status=status.HTTP_403_FORBIDDEN,
            )

        write_serializer = NotificationInboundEmailSerializer(data=request.data)
        try:
            write_serializer.is_valid(raise_exception=True)
        except serializers.ValidationError:
            record_inbound_email_ingestion(
                source=InboundEmailSource.WEBHOOK,
                status=InboundEmailIngestionStatus.REJECTED,
                sender=str(request.data.get("sender", "")).strip().lower(),
                recipient=str(request.data.get("recipient", "")).strip().lower(),
                subject=str(request.data.get("subject", "")).strip(),
                message_id=str(request.data.get("message_id", "")).strip(),
                error_message=str(write_serializer.errors),
            )
            raise

        application = write_serializer.context["application"]
        scheduled_for = write_serializer.context["scheduled_for"]
        message_id = write_serializer.validated_data.get("message_id", "")
        creation_payload = {
            "sender": write_serializer.context["normalized_sender"],
            "recipient": write_serializer.context["normalized_recipient"],
            "title": write_serializer.context["normalized_title"],
            "message": write_serializer.validated_data["text"],
            "scheduled_for": scheduled_for,
        }
        request_fingerprint = compute_request_fingerprint(creation_payload)

        outcome = create_notification_with_optional_idempotency(
            application=application,
            title=write_serializer.context["normalized_title"],
            message=write_serializer.validated_data["text"],
            scheduled_for=scheduled_for,
            idempotency_key=message_id,
            request_fingerprint=request_fingerprint,
        )

        if outcome.conflict:
            record_inbound_email_ingestion(
                source=InboundEmailSource.WEBHOOK,
                status=InboundEmailIngestionStatus.CONFLICT,
                sender=write_serializer.context["normalized_sender"],
                recipient=write_serializer.context["normalized_recipient"],
                subject=str(request.data.get("subject", "")).strip(),
                message_id=message_id,
                scheduled_for=scheduled_for,
                application=application,
                notification=outcome.notification,
                error_message="Ce message_id a deja ete utilise avec un payload different.",
            )
            return Response(
                {
                    "code": "idempotency_conflict",
                    "detail": "Ce message_id a deja ete utilise avec un payload different.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        record_inbound_email_ingestion(
            source=InboundEmailSource.WEBHOOK,
            status=InboundEmailIngestionStatus.CREATED if outcome.created else InboundEmailIngestionStatus.EXISTING,
            sender=write_serializer.context["normalized_sender"],
            recipient=write_serializer.context["normalized_recipient"],
            subject=str(request.data.get("subject", "")).strip(),
            message_id=message_id,
            scheduled_for=scheduled_for,
            application=application,
            notification=outcome.notification,
        )
        read_serializer = NotificationReadSerializer(outcome.notification)
        return Response(
            read_serializer.data,
            status=status.HTTP_201_CREATED if outcome.created else status.HTTP_200_OK,
        )
