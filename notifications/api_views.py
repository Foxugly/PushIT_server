from django.conf import settings
from django.db import OperationalError, transaction
from django.db.models import Count
from django.http import Http404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import error_response
from .models import Notification, NotificationStatus
from .utils import apply_effective_schedule_filters
from .serializers import (
    DetailResponseSerializer,
    NotificationCreateSerializer,
    NotificationCreateValidationErrorResponseSerializer,
    NotificationFutureFilterSerializer,
    NotificationFutureFilterValidationErrorResponseSerializer,
    NotificationListFilterSerializer,
    NotificationListFilterValidationErrorResponseSerializer,
    NotificationFutureUpdateSerializer,
    NotificationFutureUpdateValidationErrorResponseSerializer,
    NotificationQueuedResponseSerializer,
    NotificationReadSerializer,
    NotificationStatsSerializer,
)
from .tasks import send_notification_task

ALLOWED_NOTIFICATION_STATUSES_TO_QUEUE = {
    NotificationStatus.DRAFT,
    NotificationStatus.FAILED,
    NotificationStatus.PARTIAL,
    NotificationStatus.SCHEDULED,
}


@extend_schema_view(
    get=extend_schema(
        summary="List notifications",
        description=(
            "Returns the list of notifications for applications owned by the "
            "authenticated user. The listing can be filtered by application, "
            "status, effective date, and quiet period shift presence."
        ),
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        parameters=[
            OpenApiParameter(
                name="application_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by application.",
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
                description="List of notifications",
                examples=[
                    OpenApiExample(
                        "Shifted scheduled notifications for one application",
                        value=[
                            {
                                "id": 42,
                                "application_id": 12,
                                "application_name": "Demo Push App",
                                "title": "Evening reminder",
                                "message": "Opens at 8pm.",
                                "status": "scheduled",
                                "created_at": "2026-03-27T10:00:00Z",
                                "scheduled_for": "2026-03-27T22:30:00Z",
                                "effective_scheduled_for": "2026-03-28T08:00:00Z",
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
                                "title": "Late announcement",
                                "message": "Sending at 11pm.",
                                "status": "scheduled",
                                "created_at": "2026-03-27T10:05:00Z",
                                "scheduled_for": "2026-03-27T23:00:00Z",
                                "effective_scheduled_for": "2026-03-28T09:00:00Z",
                                "sent_at": None,
                            },
                            {
                                "id": 42,
                                "application_id": 12,
                                "application_name": "Demo Push App",
                                "title": "Evening reminder",
                                "message": "Opens at 8pm.",
                                "status": "scheduled",
                                "created_at": "2026-03-27T10:00:00Z",
                                "scheduled_for": "2026-03-27T22:30:00Z",
                                "effective_scheduled_for": "2026-03-28T08:00:00Z",
                                "sent_at": None,
                            },
                        ],
                        response_only=True,
                        status_codes=["200"],
                    ),
                ],
            ),
            400: OpenApiResponse(
                response=NotificationListFilterValidationErrorResponseSerializer,
                description="Invalid filters",
            ),
        },
    ),
    post=extend_schema(
        summary="Create a notification",
        description=(
            "Creates a new notification for an application owned by the authenticated "
            "user and an explicit list of target devices. If `scheduled_for` is "
            "provided, the notification is created with `scheduled` status and will be "
            "dispatched automatically later. `scheduled_for` represents the requested "
            "date. `effective_scheduled_for` represents the effective send date "
            "computed from currently configured quiet periods."
        ),
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        request=NotificationCreateSerializer,
        examples=[
            OpenApiExample(
                "Immediate notification",
                value={
                    "application_id": 12,
                    "device_ids": [4, 5],
                    "title": "Flash promo",
                    "message": "Available now.",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Scheduled notification",
                value={
                    "application_id": 12,
                    "device_ids": [4, 5],
                    "title": "Evening reminder",
                    "message": "Opens at 8pm.",
                    "scheduled_for": "2026-03-27T20:00:00+01:00",
                },
                request_only=True,
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=NotificationReadSerializer,
                description="Notification created",
                examples=[
                    OpenApiExample(
                        "Scheduled notification deferred by quiet period",
                        value={
                            "id": 42,
                            "application_id": 12,
                            "application_name": "Demo Push App",
                            "device_ids": [4, 5],
                            "title": "Evening reminder",
                            "message": "Opens at 8pm.",
                            "status": "scheduled",
                            "created_at": "2026-03-27T10:00:00Z",
                            "scheduled_for": "2026-03-27T22:30:00Z",
                            "effective_scheduled_for": "2026-03-28T08:00:00Z",
                            "sent_at": None,
                        },
                        response_only=True,
                        status_codes=["201"],
                    )
                ],
            ),
            400: OpenApiResponse(
                response=NotificationCreateValidationErrorResponseSerializer,
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
        },
    ),
)
class NotificationListCreateApiView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Notification.objects.filter(application__owner=self.request.user)
            .select_related("application")
            .prefetch_related("application__quiet_periods", "deliveries")
            .order_by("-id")
        )

    def get_serializer_class(self):
        if self.request.method == "POST":
            return NotificationCreateSerializer
        return NotificationReadSerializer

    def list(self, request, *args, **kwargs):
        filter_serializer = NotificationListFilterSerializer(data=request.query_params)
        filter_serializer.is_valid(raise_exception=True)
        notification_filter = filter_serializer.validated_data

        queryset = self.get_queryset()
        application_id = notification_filter.get("application_id")
        status_filter = notification_filter.get("status")
        if application_id is not None:
            queryset = queryset.filter(application_id=application_id)
        if status_filter is not None:
            queryset = queryset.filter(status=status_filter)

        queryset = list(queryset)
        queryset = apply_effective_schedule_filters(queryset, request, notification_filter)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        response_serializer = NotificationReadSerializer(
            instance,
            context=self.get_serializer_context(),
        )
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary="Notification detail",
        description="Returns the detail of a notification belonging to an application of the authenticated user.",
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        responses={
            200: NotificationReadSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Notification not found"),
        },
    ),
)
class NotificationDetailApiView(generics.RetrieveAPIView):
    serializer_class = NotificationReadSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Notification.objects.filter(application__owner=self.request.user)
            .select_related("application")
            .prefetch_related("application__quiet_periods", "deliveries")
        )

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            raise NotFound("Notification not found.", code="notification_not_found")


@extend_schema_view(
    get=extend_schema(
        summary="List future notifications",
        description=(
            "Returns only notifications with `scheduled` status whose "
            "`scheduled_for` is strictly in the future. These notifications can "
            "still be modified or deleted. `scheduled_for` represents the requested "
            "date, while `effective_scheduled_for` represents the next estimated "
            "dispatch date according to currently configured quiet periods."
        ),
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
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
                description="List of future notifications",
                examples=[
                    OpenApiExample(
                        "Future notifications shifted by quiet period",
                        value=[
                            {
                                "id": 42,
                                "application_id": 12,
                                "application_name": "Demo Push App",
                                "title": "Evening reminder",
                                "message": "Opens at 8pm.",
                                "status": "scheduled",
                                "created_at": "2026-03-27T10:00:00Z",
                                "scheduled_for": "2026-03-27T22:30:00Z",
                                "effective_scheduled_for": "2026-03-28T08:00:00Z",
                                "sent_at": None,
                            }
                        ],
                        response_only=True,
                        status_codes=["200"],
                    ),
                    OpenApiExample(
                        "Future notifications ordered by effective schedule desc",
                        value=[
                            {
                                "id": 43,
                                "application_id": 12,
                                "application_name": "Demo Push App",
                                "title": "Late announcement",
                                "message": "Sending at 11pm.",
                                "status": "scheduled",
                                "created_at": "2026-03-27T10:05:00Z",
                                "scheduled_for": "2026-03-27T23:00:00Z",
                                "effective_scheduled_for": "2026-03-28T09:00:00Z",
                                "sent_at": None,
                            },
                            {
                                "id": 42,
                                "application_id": 12,
                                "application_name": "Demo Push App",
                                "title": "Evening reminder",
                                "message": "Opens at 8pm.",
                                "status": "scheduled",
                                "created_at": "2026-03-27T10:00:00Z",
                                "scheduled_for": "2026-03-27T22:30:00Z",
                                "effective_scheduled_for": "2026-03-28T08:00:00Z",
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
                examples=[
                    OpenApiExample(
                        "Invalid effective range",
                        value={
                            "code": "validation_error",
                            "detail": "Validation error.",
                            "errors": {
                                "effective_scheduled_to": [
                                    "End bound must be after or equal to the start bound."
                                ]
                            },
                        },
                        response_only=True,
                        status_codes=["400"],
                    )
                ],
            ),
        },
    ),
)
class NotificationFutureListApiView(generics.ListAPIView):
    serializer_class = NotificationReadSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Notification.objects.filter(
                application__owner=self.request.user,
                status=NotificationStatus.SCHEDULED,
                scheduled_for__gt=timezone.now(),
            )
            .select_related("application")
            .prefetch_related("application__quiet_periods", "deliveries")
            .order_by("scheduled_for", "id")
        )

    def list(self, request, *args, **kwargs):
        filter_serializer = NotificationFutureFilterSerializer(data=request.query_params)
        filter_serializer.is_valid(raise_exception=True)
        future_filter = filter_serializer.validated_data

        queryset = list(self.get_queryset())
        queryset = apply_effective_schedule_filters(queryset, request, future_filter)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


@extend_schema_view(
    get=extend_schema(
        summary="Future notification detail",
        description=(
            "Returns a scheduled notification that is still editable. "
            "`scheduled_for` represents the date requested by the user. "
            "`effective_scheduled_for` represents the effective send date "
            "computed from current quiet periods."
        ),
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        responses={
            200: OpenApiResponse(
                response=NotificationReadSerializer,
                description="Future notification",
                examples=[
                    OpenApiExample(
                        "Future notification deferred by quiet period",
                        value={
                            "id": 42,
                            "application_id": 12,
                            "application_name": "Demo Push App",
                            "title": "Evening reminder",
                            "message": "Opens at 8pm.",
                            "status": "scheduled",
                            "created_at": "2026-03-27T10:00:00Z",
                            "scheduled_for": "2026-03-27T22:30:00Z",
                            "effective_scheduled_for": "2026-03-28T08:00:00Z",
                            "sent_at": None,
                        },
                        response_only=True,
                        status_codes=["200"],
                    )
                ],
            ),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Future notification not found"),
        },
    ),
    patch=extend_schema(
        summary="Update a future notification",
        description=(
            "Updates the content or send date of a scheduled notification. "
            "The endpoint only accepts notifications that are still in the future. "
            "Modifying a quiet period does not retroactively rewrite `scheduled_for`, "
            "but the read value `effective_scheduled_for` will reflect the effective "
            "date accounting for current quiet periods."
        ),
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        request=NotificationFutureUpdateSerializer,
        examples=[
            OpenApiExample(
                "Reschedule future notification",
                value={
                    "title": "Postponed reminder",
                    "scheduled_for": "2026-03-27T21:30:00+01:00",
                },
                request_only=True,
            )
        ],
        responses={
            200: NotificationReadSerializer,
            400: OpenApiResponse(
                response=NotificationFutureUpdateValidationErrorResponseSerializer,
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
            404: OpenApiResponse(response=DetailResponseSerializer, description="Future notification not found"),
        },
    ),
    delete=extend_schema(
        summary="Delete a future notification",
        description="Deletes a scheduled notification as long as it has not been sent.",
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        responses={
            204: None,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Future notification not found"),
        },
    ),
)
class NotificationFutureDetailApiView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "patch", "delete"]

    def get_queryset(self):
        return (
            Notification.objects.filter(
                application__owner=self.request.user,
                status=NotificationStatus.SCHEDULED,
                scheduled_for__gt=timezone.now(),
            )
            .select_related("application")
            .prefetch_related("application__quiet_periods", "deliveries")
        )

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return NotificationFutureUpdateSerializer
        return NotificationReadSerializer

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            raise NotFound("Future notification not found.", code="notification_future_not_found")


@extend_schema_view(
    post=extend_schema(
        summary="Queue a notification for sending",
        description="Schedules asynchronous sending of a notification via Celery. Future notifications (`scheduled`) cannot be manually sent until `scheduled_for` is reached.",
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        request=None,
        responses={
            202: NotificationQueuedResponseSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Notification not found"),
            409: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Notification already sent, already queued, or not sendable",
                examples=[
                    OpenApiExample(
                        "Scheduled notification not sendable yet",
                        value={
                            "code": "notification_not_sendable",
                            "detail": (
                                "Notification 42 cannot be queued "
                                "from status 'scheduled'."
                            ),
                        },
                        response_only=True,
                        status_codes=["409"],
                    )
                ],
            ),
            503: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Celery broker unavailable",
                examples=[
                    OpenApiExample(
                        "Queue unavailable",
                        value={
                            "code": "notification_queue_unavailable",
                            "detail": "Send queue is temporarily unavailable.",
                        },
                        response_only=True,
                        status_codes=["503"],
                    )
                ],
            ),
        },
    )
)
class NotificationSendApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _build_not_sendable_response(notification_id, notification_status):
        return Response(
            {
                "code": "notification_not_sendable",
                "detail": (
                    f"Notification {notification_id} cannot be queued "
                    f"from status '{notification_status}'."
                ),
            },
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _queue_notification_task(notification, previous_status):
        try:
            task = send_notification_task.delay(notification.id)
        except Exception:
            Notification.objects.filter(
                id=notification.id,
                status=NotificationStatus.QUEUED,
            ).update(status=previous_status)
            return error_response(
                code="notification_queue_unavailable",
                detail="Send queue is temporarily unavailable.",
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "status": NotificationStatus.QUEUED,
                "notification_id": notification.id,
                "task_id": task.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def _validate_and_queue(self, notification, owner_filter=None):
        if (
            notification.status == NotificationStatus.SCHEDULED
            and notification.scheduled_for is not None
            and notification.scheduled_for > timezone.now()
        ):
            return self._build_not_sendable_response(notification.id, notification.status)

        if notification.status not in ALLOWED_NOTIFICATION_STATUSES_TO_QUEUE:
            return self._build_not_sendable_response(notification.id, notification.status)

        previous_status = notification.status
        qs_filter = {"id": notification.id, "status": previous_status}
        if owner_filter:
            qs_filter.update(owner_filter)

        try:
            queued = Notification.objects.filter(**qs_filter).update(
                status=NotificationStatus.QUEUED,
            )
        except OperationalError:
            return self._build_not_sendable_response(notification.id, notification.status)

        if queued == 0:
            current_status = (
                Notification.objects.filter(id=notification.id)
                .values_list("status", flat=True)
                .first()
            )
            if current_status is None:
                return error_response(
                    code="notification_not_found",
                    detail="Notification not found.",
                    http_status=status.HTTP_404_NOT_FOUND,
                )
            return self._build_not_sendable_response(notification.id, current_status)

        notification.status = NotificationStatus.QUEUED
        return self._queue_notification_task(notification, previous_status)

    def _fetch_notification(self, notification_id, owner_filter):
        if settings.DB_SUPPORTS_ROW_LOCKING:
            with transaction.atomic():
                return (
                    Notification.objects.select_for_update()
                    .select_related("application")
                    .get(id=notification_id, **owner_filter)
                )
        return (
            Notification.objects.select_related("application")
            .get(id=notification_id, **owner_filter)
        )

    def post(self, request, notification_id):
        owner_filter = {"application__owner": request.user}

        try:
            notification = self._fetch_notification(notification_id, owner_filter)
        except Notification.DoesNotExist:
            return error_response(
                code="notification_not_found",
                detail="Notification not found.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        return self._validate_and_queue(
            notification,
            owner_filter=owner_filter if not settings.DB_SUPPORTS_ROW_LOCKING else None,
        )


@extend_schema_view(
    get=extend_schema(
        summary="List notification statistics",
        description="Returns the notification count by status for applications owned by the authenticated user. Optionally filtered by application.",
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        parameters=[
            OpenApiParameter(
                name="application_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter statistics by application.",
            ),
        ],
        responses={200: NotificationStatsSerializer(many=True)},
    ),
)
class NotificationStatsApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        queryset = Notification.objects.filter(application__owner=request.user)
        application_id = request.query_params.get("application_id")
        if application_id is not None:
            queryset = queryset.filter(application_id=application_id)
        stats = (
            queryset
            .values("status")
            .annotate(count=Count("id"))
            .order_by("status")
        )
        return Response(NotificationStatsSerializer(stats, many=True).data)
