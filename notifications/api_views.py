from django.db import OperationalError, connection, transaction
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
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import error_response
from .models import Notification, NotificationStatus
from .scheduling import (
    compute_effective_scheduled_map,
    filter_notifications_by_effective_range,
    filter_notifications_by_shift_flag,
    order_notifications_by_effective,
)
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
        summary="Lister les notifications",
        description=(
            "Retourne la liste des notifications des applications appartenant a "
            "l'utilisateur connecte. Le listing peut etre filtre par application, "
            "statut, date effective et presence d'un decalage du a une periode "
            "blanche."
        ),
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        parameters=[
            OpenApiParameter(
                name="application_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filtre par application.",
            ),
            OpenApiParameter(
                name="status",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=[choice for choice, _ in NotificationStatus.choices],
                description="Filtre par statut de notification.",
            ),
            OpenApiParameter(
                name="effective_scheduled_from",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filtre inclusif sur la date effective minimale d'envoi.",
            ),
            OpenApiParameter(
                name="effective_scheduled_to",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filtre inclusif sur la date effective maximale d'envoi.",
            ),
            OpenApiParameter(
                name="has_quiet_period_shift",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Ne retourne que les notifications dont la date effective est decalee par une periode blanche.",
            ),
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=["effective_scheduled_for", "-effective_scheduled_for"],
                description="Tri optionnel par date effective d'envoi.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=NotificationReadSerializer(many=True),
                description="Liste des notifications",
                examples=[
                    OpenApiExample(
                        "Shifted scheduled notifications for one application",
                        value=[
                            {
                                "id": 42,
                                "application_id": 12,
                                "application_name": "Demo Push App",
                                "title": "Rappel ce soir",
                                "message": "Ouverture a 20h.",
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
                                "title": "Annonce tardive",
                                "message": "Envoi a 23h.",
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
                                "title": "Rappel ce soir",
                                "message": "Ouverture a 20h.",
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
                description="Filtres invalides",
            ),
        },
    ),
    post=extend_schema(
        summary="Creer une notification",
        description=(
            "Cree une nouvelle notification pour une application appartenant a "
            "l'utilisateur connecte. Si `scheduled_for` est fourni, la notification "
            "est creee en statut `scheduled` et sera dispatchee automatiquement plus "
            "tard. `scheduled_for` represente la date demandee. "
            "`effective_scheduled_for` represente la date effective d'envoi calculee "
            "a partir des periodes blanches actuellement configurees."
        ),
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        request=NotificationCreateSerializer,
        examples=[
            OpenApiExample(
                "Immediate notification",
                value={
                    "application_id": 12,
                    "title": "Promo flash",
                    "message": "Disponible maintenant.",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Scheduled notification",
                value={
                    "application_id": 12,
                    "title": "Rappel ce soir",
                    "message": "Ouverture a 20h.",
                    "scheduled_for": "2026-03-27T20:00:00+01:00",
                },
                request_only=True,
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=NotificationReadSerializer,
                description="Notification creee",
                examples=[
                    OpenApiExample(
                        "Scheduled notification deferred by quiet period",
                        value={
                            "id": 42,
                            "application_id": 12,
                            "application_name": "Demo Push App",
                            "title": "Rappel ce soir",
                            "message": "Ouverture a 20h.",
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
                description="Donnees invalides",
                examples=[
                    OpenApiExample(
                        "Validation error",
                        value={
                            "code": "validation_error",
                            "detail": "Validation error.",
                            "errors": {
                                "scheduled_for": [
                                    "La date planifiee doit etre dans le futur."
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
            .prefetch_related("application__quiet_periods")
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
        effective_scheduled_from = notification_filter.get("effective_scheduled_from")
        effective_scheduled_to = notification_filter.get("effective_scheduled_to")
        has_quiet_period_shift = (
            notification_filter.get("has_quiet_period_shift")
            if "has_quiet_period_shift" in request.query_params
            else None
        )
        ordering = notification_filter.get("ordering")
        effective_scheduled_map = None

        if (
            effective_scheduled_from is not None
            or effective_scheduled_to is not None
            or has_quiet_period_shift is not None
            or ordering
        ):
            effective_scheduled_map = compute_effective_scheduled_map(queryset)

        if effective_scheduled_from is not None or effective_scheduled_to is not None:
            queryset = filter_notifications_by_effective_range(
                queryset,
                effective_scheduled_map,
                effective_scheduled_from=effective_scheduled_from,
                effective_scheduled_to=effective_scheduled_to,
            )

        if has_quiet_period_shift is not None:
            queryset = filter_notifications_by_shift_flag(
                queryset,
                effective_scheduled_map,
                has_quiet_period_shift,
            )

        if ordering:
            queryset = order_notifications_by_effective(queryset, effective_scheduled_map, ordering)

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
        summary="Detail d'une notification",
        description="Retourne le detail d'une notification appartenant a une application de l'utilisateur connecte.",
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        responses={
            200: NotificationReadSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Notification introuvable"),
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
            .prefetch_related("application__quiet_periods")
        )

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            raise NotFound("Notification introuvable.", code="notification_not_found")


@extend_schema_view(
    get=extend_schema(
        summary="Lister les notifications futures",
        description=(
            "Retourne uniquement les notifications en statut `scheduled` dont "
            "`scheduled_for` est strictement dans le futur. Ces notifications peuvent "
            "encore etre modifiees ou supprimees. `scheduled_for` represente la date "
            "demandee, tandis que `effective_scheduled_for` represente la prochaine "
            "date de dispatch estimee selon les periodes blanches actuellement "
            "configurees."
        ),
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        parameters=[
            OpenApiParameter(
                name="effective_scheduled_from",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filtre inclusif sur la date effective minimale d'envoi.",
            ),
            OpenApiParameter(
                name="effective_scheduled_to",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filtre inclusif sur la date effective maximale d'envoi.",
            ),
            OpenApiParameter(
                name="has_quiet_period_shift",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Ne retourne que les notifications dont la date effective est decalee par une periode blanche.",
            ),
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=["effective_scheduled_for", "-effective_scheduled_for"],
                description="Tri optionnel par date effective d'envoi.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=NotificationReadSerializer(many=True),
                description="Liste des notifications futures",
                examples=[
                    OpenApiExample(
                        "Future notifications shifted by quiet period",
                        value=[
                            {
                                "id": 42,
                                "application_id": 12,
                                "application_name": "Demo Push App",
                                "title": "Rappel ce soir",
                                "message": "Ouverture a 20h.",
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
                                "title": "Annonce tardive",
                                "message": "Envoi a 23h.",
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
                                "title": "Rappel ce soir",
                                "message": "Ouverture a 20h.",
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
                description="Filtres invalides",
                examples=[
                    OpenApiExample(
                        "Invalid effective range",
                        value={
                            "code": "validation_error",
                            "detail": "Validation error.",
                            "errors": {
                                "effective_scheduled_to": [
                                    "La borne de fin doit etre apres ou egale a la borne de debut."
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
            .prefetch_related("application__quiet_periods")
            .order_by("scheduled_for", "id")
        )

    def list(self, request, *args, **kwargs):
        filter_serializer = NotificationFutureFilterSerializer(data=request.query_params)
        filter_serializer.is_valid(raise_exception=True)
        future_filter = filter_serializer.validated_data

        queryset = list(self.get_queryset())
        effective_scheduled_from = future_filter.get("effective_scheduled_from")
        effective_scheduled_to = future_filter.get("effective_scheduled_to")
        has_quiet_period_shift = (
            future_filter.get("has_quiet_period_shift")
            if "has_quiet_period_shift" in request.query_params
            else None
        )
        ordering = future_filter.get("ordering")
        effective_scheduled_map = None

        if (
            effective_scheduled_from is not None
            or effective_scheduled_to is not None
            or has_quiet_period_shift is not None
            or ordering
        ):
            effective_scheduled_map = compute_effective_scheduled_map(queryset)

        if effective_scheduled_from is not None or effective_scheduled_to is not None:
            queryset = filter_notifications_by_effective_range(
                queryset,
                effective_scheduled_map,
                effective_scheduled_from=effective_scheduled_from,
                effective_scheduled_to=effective_scheduled_to,
            )

        if has_quiet_period_shift is not None:
            queryset = filter_notifications_by_shift_flag(
                queryset,
                effective_scheduled_map,
                has_quiet_period_shift,
            )

        if ordering:
            queryset = order_notifications_by_effective(queryset, effective_scheduled_map, ordering)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


@extend_schema_view(
    get=extend_schema(
        summary="Detail d'une notification future",
        description=(
            "Retourne une notification planifiee encore modifiable. "
            "`scheduled_for` represente la date demandee par l'utilisateur. "
            "`effective_scheduled_for` represente la date effective d'envoi calculee "
            "a partir des periodes blanches courantes."
        ),
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        responses={
            200: OpenApiResponse(
                response=NotificationReadSerializer,
                description="Notification future",
                examples=[
                    OpenApiExample(
                        "Future notification deferred by quiet period",
                        value={
                            "id": 42,
                            "application_id": 12,
                            "application_name": "Demo Push App",
                            "title": "Rappel ce soir",
                            "message": "Ouverture a 20h.",
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
            404: OpenApiResponse(response=DetailResponseSerializer, description="Notification future introuvable"),
        },
    ),
    patch=extend_schema(
        summary="Modifier une notification future",
        description=(
            "Modifie le contenu ou la date d'envoi d'une notification planifiee. "
            "L'endpoint n'accepte que les notifications encore futures. Modifier une "
            "periode blanche ne reecrit pas retroactivement `scheduled_for`, mais la "
            "valeur de lecture `effective_scheduled_for` refletera la date effective "
            "tenant compte des periodes blanches actuelles."
        ),
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        request=NotificationFutureUpdateSerializer,
        examples=[
            OpenApiExample(
                "Reschedule future notification",
                value={
                    "title": "Rappel repousse",
                    "scheduled_for": "2026-03-27T21:30:00+01:00",
                },
                request_only=True,
            )
        ],
        responses={
            200: NotificationReadSerializer,
            400: OpenApiResponse(
                response=NotificationFutureUpdateValidationErrorResponseSerializer,
                description="Donnees invalides",
                examples=[
                    OpenApiExample(
                        "Validation error",
                        value={
                            "code": "validation_error",
                            "detail": "Validation error.",
                            "errors": {
                                "scheduled_for": [
                                    "La date planifiee doit etre dans le futur."
                                ]
                            },
                        },
                        response_only=True,
                        status_codes=["400"],
                    )
                ],
            ),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Notification future introuvable"),
        },
    ),
    delete=extend_schema(
        summary="Supprimer une notification future",
        description="Supprime une notification planifiee tant qu'elle n'a pas ete envoyee.",
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        responses={
            204: None,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Notification future introuvable"),
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
            .prefetch_related("application__quiet_periods")
        )

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return NotificationFutureUpdateSerializer
        return NotificationReadSerializer

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            raise NotFound("Notification future introuvable.", code="notification_future_not_found")


@extend_schema_view(
    post=extend_schema(
        summary="Mettre une notification en file d'envoi",
        description="Planifie l'envoi asynchrone d'une notification via Celery. Les notifications futures (`scheduled`) ne sont pas envoyables manuellement tant que `scheduled_for` n'est pas atteint.",
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        request=None,
        responses={
            202: NotificationQueuedResponseSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Notification introuvable"),
            409: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Notification deja envoyee, deja en file ou non envoyable",
                examples=[
                    OpenApiExample(
                        "Scheduled notification not sendable yet",
                        value={
                            "code": "notification_not_sendable",
                            "detail": (
                                "La notification 42 ne peut pas etre mise en file "
                                "depuis le statut 'scheduled'."
                            ),
                        },
                        response_only=True,
                        status_codes=["409"],
                    )
                ],
            ),
            503: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Broker Celery indisponible",
                examples=[
                    OpenApiExample(
                        "Queue unavailable",
                        value={
                            "code": "notification_queue_unavailable",
                            "detail": "La file d'envoi est temporairement indisponible.",
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
                    f"La notification {notification_id} ne peut pas être mise en file "
                    f"depuis le statut '{notification_status}'."
                ),
            },
            status=status.HTTP_409_CONFLICT,
        )

    def post(self, request, notification_id):
        try:
            with transaction.atomic():
                notification = (
                    Notification.objects.select_for_update()
                    .select_related("application")
                    .get(id=notification_id, application__owner=request.user)
                )

                if (
                    notification.status == NotificationStatus.SCHEDULED
                    and notification.scheduled_for is not None
                    and notification.scheduled_for > timezone.now()
                ):
                    return self._build_not_sendable_response(notification.id, notification.status)

                if notification.status not in ALLOWED_NOTIFICATION_STATUSES_TO_QUEUE:
                    return self._build_not_sendable_response(notification.id, notification.status)

                previous_status = notification.status
                notification.status = NotificationStatus.QUEUED
                notification.save(update_fields=["status"])
        except OperationalError:
            if connection.vendor == "sqlite":
                current_status = (
                    Notification.objects.filter(id=notification_id, application__owner=request.user)
                    .values_list("status", flat=True)
                    .first()
                )
                if current_status is None:
                    return error_response(
                        code="notification_not_found",
                        detail="Notification introuvable.",
                        http_status=status.HTTP_404_NOT_FOUND,
                    )
                if current_status not in ALLOWED_NOTIFICATION_STATUSES_TO_QUEUE:
                    return self._build_not_sendable_response(notification_id, current_status)
            raise
        except Notification.DoesNotExist:
            return error_response(
                code="notification_not_found",
                detail="Notification introuvable.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        try:
            task = send_notification_task.delay(notification.id)
        except Exception:
            Notification.objects.filter(
                id=notification.id,
                status=NotificationStatus.QUEUED,
            ).update(status=previous_status)
            return error_response(
                code="notification_queue_unavailable",
                detail="La file d'envoi est temporairement indisponible.",
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


@extend_schema_view(
    get=extend_schema(
        summary="Lister les statistiques des notifications",
        description="Retourne le nombre de notifications par statut pour les applications appartenant a l'utilisateur connecte.",
        tags=["Notifications"],
        auth=[{"BearerAuth": []}],
        responses={200: NotificationStatsSerializer(many=True)},
    ),
)
class NotificationStatsApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        stats = (
            Notification.objects.filter(application__owner=request.user)
            .values("status")
            .annotate(count=Count("id"))
            .order_by("status")
        )
        return Response(NotificationStatsSerializer(stats, many=True).data)
