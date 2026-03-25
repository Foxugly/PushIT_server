from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.response import Response

from applications.authentication import AppTokenAuthentication
from applications.permissions import HasAppToken
from config.api_errors import error_response
from .models import Notification, NotificationStatus
from .serializers import (
    DetailResponseSerializer,
    NotificationCreateWithAppTokenSerializer,
    NotificationCreateWithAppTokenValidationErrorResponseSerializer,
    NotificationReadSerializer,
)
from .utils import compute_request_fingerprint


@extend_schema_view(
    post=extend_schema(
        summary="Creer une notification via app token",
        description="Cree une nouvelle notification pour l'application authentifiee via le header `X-App-Token`.",
        tags=["Notifications"],
        request=NotificationCreateWithAppTokenSerializer,
        responses={
            200: NotificationReadSerializer,
            201: NotificationReadSerializer,
            400: OpenApiResponse(response=NotificationCreateWithAppTokenValidationErrorResponseSerializer, description="Donnees invalides"),
            401: OpenApiResponse(response=DetailResponseSerializer, description="App token invalide ou manquant"),
            403: OpenApiResponse(response=DetailResponseSerializer, description="Acces refuse"),
            409: OpenApiResponse(response=DetailResponseSerializer, description="Cle d'idempotence deja utilisee avec un payload different"),
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

        try:
            with transaction.atomic():
                instance = Notification.objects.create(
                    application=application,
                    title=validated_data["title"],
                    message=validated_data["message"],
                    status=NotificationStatus.DRAFT,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                )
        except IntegrityError:
            instance = Notification.objects.get(application=application, idempotency_key=idempotency_key)

            if instance.request_fingerprint != request_fingerprint:
                return Response(
                    {
                        "code": "idempotency_conflict",
                        "detail": "Cette clé d'idempotence a déjà été utilisée avec un payload différent.",
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            read_serializer = NotificationReadSerializer(instance, context=self.get_serializer_context())
            return Response(read_serializer.data, status=status.HTTP_200_OK)

        read_serializer = NotificationReadSerializer(instance, context=self.get_serializer_context())
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary="Lister les notifications via app token",
        description="Retourne la liste des notifications de l'application authentifiee via le header `X-App-Token`.",
        tags=["Notifications"],
        responses={
            200: NotificationReadSerializer(many=True),
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
            .order_by("-id")
        )
