from django.db import OperationalError, connection, transaction
from django.db.models import Count
from django.http import Http404
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import error_response
from .models import Notification, NotificationStatus
from .serializers import (
    DetailResponseSerializer,
    NotificationCreateSerializer,
    NotificationCreateValidationErrorResponseSerializer,
    NotificationQueuedResponseSerializer,
    NotificationReadSerializer,
    NotificationStatsSerializer,
)
from .tasks import send_notification_task

ALLOWED_NOTIFICATION_STATUSES_TO_QUEUE = {
    NotificationStatus.DRAFT,
    NotificationStatus.FAILED,
    NotificationStatus.PARTIAL,
}


@extend_schema_view(
    get=extend_schema(
        summary="Lister les notifications",
        description="Retourne la liste des notifications des applications appartenant a l'utilisateur connecte.",
        tags=["Notifications"],
        responses={200: NotificationReadSerializer(many=True)},
    ),
    post=extend_schema(
        summary="Creer une notification",
        description="Cree une nouvelle notification pour une application appartenant a l'utilisateur connecte.",
        tags=["Notifications"],
        request=NotificationCreateSerializer,
        responses={
            201: NotificationReadSerializer,
            400: OpenApiResponse(
                response=NotificationCreateValidationErrorResponseSerializer,
                description="Donnees invalides",
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
            .order_by("-id")
        )

    def get_serializer_class(self):
        if self.request.method == "POST":
            return NotificationCreateSerializer
        return NotificationReadSerializer

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
        )

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            raise NotFound("Notification introuvable.", code="notification_not_found")


@extend_schema_view(
    post=extend_schema(
        summary="Mettre une notification en file d'envoi",
        description="Planifie l'envoi asynchrone d'une notification via Celery.",
        tags=["Notifications"],
        request=None,
        responses={
            202: NotificationQueuedResponseSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Notification introuvable"),
            409: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Notification deja envoyee, deja en file ou non envoyable",
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

                if notification.status not in ALLOWED_NOTIFICATION_STATUSES_TO_QUEUE:
                    return self._build_not_sendable_response(notification.id, notification.status)

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

        task = send_notification_task.delay(notification.id)
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
