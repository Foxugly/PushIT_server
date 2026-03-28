from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.response import Response

from applications.authentication import AppTokenAuthentication
from applications.permissions import HasAppToken
from config.api_errors import error_response
from .creation import create_notification_with_optional_idempotency
from .models import Notification, NotificationStatus
from .serializers import (
    DetailResponseSerializer,
    NotificationCreateWithAppTokenSerializer,
    NotificationCreateWithAppTokenValidationErrorResponseSerializer,
    NotificationFutureFilterSerializer,
    NotificationFutureFilterValidationErrorResponseSerializer,
    NotificationListFilterSerializer,
    NotificationReadSerializer,
)
from .scheduling import (
    compute_effective_scheduled_map,
    filter_notifications_by_effective_range,
    filter_notifications_by_shift_flag,
    order_notifications_by_effective,
)
from .utils import compute_request_fingerprint


@extend_schema_view(
    post=extend_schema(
        summary="Creer une notification via app token",
        description=(
            "Cree une nouvelle notification pour l'application authentifiee via le "
            "header `X-App-Token`. Si `scheduled_for` est fourni, la notification "
            "est creee en statut `scheduled`. `scheduled_for` represente la date "
            "demandee. `effective_scheduled_for` represente la date effective "
            "d'envoi calculee a partir des periodes blanches actuellement configurees. "
            "Le header `Idempotency-Key` est obligatoire."
        ),
        tags=["Notifications"],
        auth=[{"AppTokenAuth": []}],
        parameters=[
            OpenApiParameter(
                name="Idempotency-Key",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description="Clé d'idempotence obligatoire pour dédupliquer les créations côté application.",
            )
        ],
        request=NotificationCreateWithAppTokenSerializer,
        examples=[
            OpenApiExample(
                "Scheduled app-token notification",
                value={
                    "title": "Offre du soir",
                    "message": "Disponible à partir de 19h.",
                    "scheduled_for": "2026-03-27T19:00:00+01:00",
                },
                request_only=True,
            )
        ],
        responses={
            200: OpenApiResponse(
                response=NotificationReadSerializer,
                description="Notification existante retournee grace a l'idempotence",
                examples=[
                    OpenApiExample(
                        "Existing scheduled notification deferred by quiet period",
                        value={
                            "id": 42,
                            "application_id": 12,
                            "application_name": "Demo Push App",
                            "title": "Offre du soir",
                            "message": "Disponible a partir de 19h.",
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
                description="Notification creee",
                examples=[
                    OpenApiExample(
                        "Created scheduled notification deferred by quiet period",
                        value={
                            "id": 42,
                            "application_id": 12,
                            "application_name": "Demo Push App",
                            "title": "Offre du soir",
                            "message": "Disponible a partir de 19h.",
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
            401: OpenApiResponse(response=DetailResponseSerializer, description="App token invalide ou manquant"),
            403: OpenApiResponse(response=DetailResponseSerializer, description="Acces refuse"),
            409: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Cle d'idempotence deja utilisee avec un payload different",
                examples=[
                    OpenApiExample(
                        "Idempotency conflict",
                        value={
                            "code": "idempotency_conflict",
                            "detail": (
                                "Cette cle d'idempotence a deja ete utilisee "
                                "avec un payload different."
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
                detail="Header Idempotency-Key manquant.",
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
                    "detail": "Cette clé d'idempotence a déjà été utilisée avec un payload différent.",
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
        summary="Lister les notifications via app token",
        description=(
            "Retourne la liste des notifications de l'application authentifiee via "
            "le header `X-App-Token`. Les filtres "
            "`effective_scheduled_from` / `effective_scheduled_to` s'appliquent a "
            "`effective_scheduled_for`. Les filtres `status` et "
            "`has_quiet_period_shift` sont aussi disponibles. Le parametre "
            "`ordering` permet de trier par date effective d'envoi."
        ),
        tags=["Notifications"],
        auth=[{"AppTokenAuth": []}],
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
                name="status",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=[choice for choice, _ in NotificationStatus.choices],
                description="Filtre par statut de notification.",
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
                description="Liste des notifications de l'application",
                examples=[
                    OpenApiExample(
                        "Notifications shifted by quiet period",
                        value=[
                            {
                                "id": 42,
                                "application_id": 12,
                                "application_name": "Demo Push App",
                                "title": "Offre du soir",
                                "message": "Disponible a partir de 19h.",
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
                                "title": "Offre tardive",
                                "message": "Disponible plus tard dans la nuit.",
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
                                "title": "Offre du soir",
                                "message": "Disponible a partir de 19h.",
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
                description="Filtres invalides",
            ),
            401: OpenApiResponse(response=DetailResponseSerializer, description="App token invalide ou manquant"),
            403: OpenApiResponse(response=DetailResponseSerializer, description="Acces refuse"),
        },
    ),
)
class NotificationListWithAppTokenApiView(generics.ListAPIView):
    authentication_classes = [AppTokenAuthentication]
    permission_classes = [HasAppToken]
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
        effective_scheduled_from = list_filter.get("effective_scheduled_from")
        effective_scheduled_to = list_filter.get("effective_scheduled_to")
        has_quiet_period_shift = (
            list_filter.get("has_quiet_period_shift")
            if "has_quiet_period_shift" in request.query_params
            else None
        )
        ordering = list_filter.get("ordering")
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
