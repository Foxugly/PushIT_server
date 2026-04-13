from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view, inline_serializer
from rest_framework import generics, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from applications.authentication import AppTokenAuthentication
from applications.permissions import HasAppToken
from applications.throttles import AppTokenRateThrottle
from config.api_errors import error_response
from .creation import create_notification_with_optional_idempotency
from .models import Notification, NotificationStatus
from .serializers import (
    DetailResponseSerializer,
    NotificationCreateWithAppTokenSerializer,
    NotificationCreateWithAppTokenValidationErrorResponseSerializer,
    NotificationFutureFilterValidationErrorResponseSerializer,
    NotificationListFilterSerializer,
    NotificationReadSerializer,
)
from .utils import apply_effective_schedule_filters, compute_request_fingerprint


@extend_schema_view(
    post=extend_schema(
        summary="Create a notification via app token",
        description=(
            "Creates a new notification for the application authenticated via the "
            "`X-App-Token` header. If `scheduled_for` is provided, the notification "
            "is created with `scheduled` status. `scheduled_for` represents the "
            "requested date. `effective_scheduled_for` represents the effective send "
            "date computed from currently configured quiet periods. "
            "The `Idempotency-Key` header is required."
        ),
        tags=["Notifications"],
        auth=[{"AppTokenAuth": []}],
        parameters=[
            OpenApiParameter(
                name="Idempotency-Key",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description="Required idempotency key to deduplicate creations on the application side.",
            )
        ],
        request=NotificationCreateWithAppTokenSerializer,
        examples=[
            OpenApiExample(
                "Scheduled app-token notification",
                value={
                    "title": "Evening offer",
                    "message": "Available from 7pm.",
                    "scheduled_for": "2026-03-27T19:00:00+01:00",
                },
                request_only=True,
            )
        ],
        responses={
            200: OpenApiResponse(
                response=NotificationReadSerializer,
                description="Existing notification returned via idempotency",
                examples=[
                    OpenApiExample(
                        "Existing scheduled notification deferred by quiet period",
                        value={
                            "id": 42,
                            "application_id": 12,
                            "application_name": "Demo Push App",
                            "title": "Evening offer",
                            "message": "Available from 7pm.",
                            "status": "scheduled",
                            "created_at": "2026-03-27T10:00:00Z",
                            "scheduled_for": "2026-03-27T19:00:00Z",
                            "effective_scheduled_for": "2026-03-27T22:00:00Z",
                            "sent_at": None,
                        },
                        response_only=True,
                        status_codes=["200"],
                    )
                ],
            ),
            201: OpenApiResponse(
                response=NotificationReadSerializer,
                description="Notification created",
                examples=[
                    OpenApiExample(
                        "Created scheduled notification deferred by quiet period",
                        value={
                            "id": 42,
                            "application_id": 12,
                            "application_name": "Demo Push App",
                            "title": "Evening offer",
                            "message": "Available from 7pm.",
                            "status": "scheduled",
                            "created_at": "2026-03-27T10:00:00Z",
                            "scheduled_for": "2026-03-27T19:00:00Z",
                            "effective_scheduled_for": "2026-03-27T22:00:00Z",
                            "sent_at": None,
                        },
                        response_only=True,
                        status_codes=["201"],
                    )
                ],
            ),
            400: OpenApiResponse(
                response=NotificationCreateWithAppTokenValidationErrorResponseSerializer,
                description="Invalid data",
                examples=[
                    OpenApiExample(
                        "Validation error",
                        value={
                            "code": "validation_error",
                            "detail": "Validation error.",
                            "errors": {
                                "scheduled_for": [
                                    "Scheduled date must be in the future."
                                ]
                            },
                        },
                        response_only=True,
                        status_codes=["400"],
                    )
                ],
            ),
            401: OpenApiResponse(response=DetailResponseSerializer, description="Invalid or missing app token"),
            403: OpenApiResponse(response=DetailResponseSerializer, description="Access denied"),
            409: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Idempotency key already used with a different payload",
                examples=[
                    OpenApiExample(
                        "Idempotency conflict",
                        value={
                            "code": "idempotency_conflict",
                            "detail": (
                                "This idempotency key has already been used "
                                "with a different payload."
                            ),
                        },
                        response_only=True,
                        status_codes=["409"],
                    )
                ],
            ),
        },
    ),
)
class NotificationCreateWithAppTokenApiView(generics.GenericAPIView):
    authentication_classes = [AppTokenAuthentication]
    permission_classes = [HasAppToken]
    throttle_classes = [AppTokenRateThrottle]
    serializer_class = NotificationCreateWithAppTokenSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["application"] = self.request.auth_application
        return context

    def post(self, request, *args, **kwargs):
        write_serializer = self.get_serializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)

        application = request.auth_application
        validated_data = write_serializer.validated_data

        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            return error_response(
                code="idempotency_key_missing",
                detail="Missing Idempotency-Key header.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        request_fingerprint = compute_request_fingerprint(validated_data)
        outcome = create_notification_with_optional_idempotency(
            application=application,
            title=validated_data["title"],
            message=validated_data["message"],
            scheduled_for=validated_data.get("scheduled_for"),
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )

        if outcome.conflict:
            return Response(
                {
                    "code": "idempotency_conflict",
                    "detail": "This idempotency key has already been used with a different payload.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        read_serializer = NotificationReadSerializer(outcome.notification, context=self.get_serializer_context())
        return Response(
            read_serializer.data,
            status=status.HTTP_201_CREATED if outcome.created else status.HTTP_200_OK,
        )


@extend_schema_view(
    get=extend_schema(
        summary="List notifications via app token",
        description=(
            "Returns the list of notifications for the application authenticated via "
            "the `X-App-Token` header. Filters `effective_scheduled_from` / "
            "`effective_scheduled_to` apply to `effective_scheduled_for`. Filters "
            "`status` and `has_quiet_period_shift` are also available. The `ordering` "
            "parameter allows sorting by effective send date."
        ),
        tags=["Notifications"],
        auth=[{"AppTokenAuth": []}],
        parameters=[
            OpenApiParameter(
                name="effective_scheduled_from",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Inclusive filter on minimum effective send date.",
            ),
            OpenApiParameter(
                name="effective_scheduled_to",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Inclusive filter on maximum effective send date.",
            ),
            OpenApiParameter(
                name="status",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=[choice for choice, _ in NotificationStatus.choices],
                description="Filter by notification status.",
            ),
            OpenApiParameter(
                name="has_quiet_period_shift",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Only return notifications whose effective date is shifted by a quiet period.",
            ),
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=["effective_scheduled_for", "-effective_scheduled_for"],
                description="Optional ordering by effective send date.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=NotificationReadSerializer(many=True),
                description="Application notification list",
                examples=[
                    OpenApiExample(
                        "Notifications shifted by quiet period",
                        value=[
                            {
                                "id": 42,
                                "application_id": 12,
                                "application_name": "Demo Push App",
                                "title": "Evening offer",
                                "message": "Available from 7pm.",
                                "status": "scheduled",
                                "created_at": "2026-03-27T10:00:00Z",
                                "scheduled_for": "2026-03-27T19:00:00Z",
                                "effective_scheduled_for": "2026-03-27T22:00:00Z",
                                "sent_at": None,
                            }
                        ],
                        response_only=True,
                        status_codes=["200"],
                    ),
                    OpenApiExample(
                        "Notifications ordered by effective schedule desc",
                        value=[
                            {
                                "id": 43,
                                "application_id": 12,
                                "application_name": "Demo Push App",
                                "title": "Late offer",
                                "message": "Available later tonight.",
                                "status": "scheduled",
                                "created_at": "2026-03-27T10:05:00Z",
                                "scheduled_for": "2026-03-27T21:30:00Z",
                                "effective_scheduled_for": "2026-03-27T23:00:00Z",
                                "sent_at": None,
                            },
                            {
                                "id": 42,
                                "application_id": 12,
                                "application_name": "Demo Push App",
                                "title": "Evening offer",
                                "message": "Available from 7pm.",
                                "status": "scheduled",
                                "created_at": "2026-03-27T10:00:00Z",
                                "scheduled_for": "2026-03-27T19:00:00Z",
                                "effective_scheduled_for": "2026-03-27T22:00:00Z",
                                "sent_at": None,
                            },
                        ],
                        response_only=True,
                        status_codes=["200"],
                    ),
                ],
            ),
            400: OpenApiResponse(
                response=NotificationFutureFilterValidationErrorResponseSerializer,
                description="Invalid filters",
            ),
            401: OpenApiResponse(response=DetailResponseSerializer, description="Invalid or missing app token"),
            403: OpenApiResponse(response=DetailResponseSerializer, description="Access denied"),
        },
    ),
)
class NotificationListWithAppTokenApiView(generics.ListAPIView):
    authentication_classes = [AppTokenAuthentication]
    permission_classes = [HasAppToken]
    throttle_classes = [AppTokenRateThrottle]
    serializer_class = NotificationReadSerializer

    def get_queryset(self):
        return (
            Notification.objects.filter(application=self.request.auth_application)
            .select_related("application")
            .prefetch_related("application__quiet_periods", "deliveries")
            .order_by("-id")
        )

    def list(self, request, *args, **kwargs):
        filter_serializer = NotificationListFilterSerializer(data=request.query_params)
        filter_serializer.is_valid(raise_exception=True)
        list_filter = filter_serializer.validated_data

        queryset = self.get_queryset()
        status_filter = list_filter.get("status")
        if status_filter is not None:
            queryset = queryset.filter(status=status_filter)

        queryset = list(queryset)
        queryset = apply_effective_schedule_filters(queryset, request, list_filter)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class NotificationBulkSendWithAppTokenApiView(APIView):
    authentication_classes = [AppTokenAuthentication]
    permission_classes = [HasAppToken]
    throttle_classes = [AppTokenRateThrottle]

    @extend_schema(
        summary="Bulk queue notifications via app token",
        description="Queues multiple notifications for async sending. Returns queued IDs and per-notification errors.",
        tags=["Notifications"],
        auth=[{"AppTokenAuth": []}],
        request=inline_serializer(
            name="BulkSendAppTokenRequest",
            fields={"notification_ids": serializers.ListField(child=serializers.IntegerField())},
        ),
        responses={
            200: OpenApiResponse(description="Bulk send result with queued and errors"),
            401: OpenApiResponse(response=DetailResponseSerializer, description="Invalid or missing app token"),
        },
    )
    def post(self, request):
        from .api_views import _try_queue_notification

        notification_ids = request.data.get("notification_ids", [])
        if not notification_ids:
            return error_response(
                code="validation_error",
                detail="notification_ids is required and cannot be empty.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        application = request.auth_application
        use_owner_in_queue = not settings.DB_SUPPORTS_ROW_LOCKING
        owner_filter = {"application": application}

        notifications = (
            Notification.objects.select_related("application")
            .filter(id__in=notification_ids, application=application)
        )
        found = {n.id: n for n in notifications}

        queued = []
        errors = []

        for nid in notification_ids:
            if nid not in found:
                errors.append({"id": nid, "code": "notification_not_found", "detail": "Notification not found."})
                continue
            success, result = _try_queue_notification(
                found[nid],
                owner_filter=owner_filter if use_owner_in_queue else None,
            )
            if success:
                queued.append(result["notification_id"])
            else:
                errors.append(result)

        return Response({"queued": queued, "errors": errors}, status=status.HTTP_200_OK)
