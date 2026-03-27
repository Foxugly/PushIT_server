from django.http import Http404
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from .models import Device, DeviceQuietPeriod
from .serializers import (
    DetailResponseSerializer,
    DeviceReadSerializer,
    DeviceQuietPeriodReadSerializer,
    DeviceQuietPeriodValidationErrorResponseSerializer,
    DeviceQuietPeriodWriteSerializer,
    DeviceUpdateSerializer,
    DeviceUpdateValidationErrorResponseSerializer,
)


@extend_schema_view(
    get=extend_schema(
        summary="Lister les devices",
        description="Retourne la liste des devices appartenant a l'utilisateur connecte.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={200: DeviceReadSerializer(many=True)},
    ),
)
class DeviceListApiView(generics.ListAPIView):
    serializer_class = DeviceReadSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Device.objects.filter(
                application_links__application__owner=self.request.user,
                application_links__is_active=True,
            )
            .distinct()
            .order_by("-id")
        )


@extend_schema_view(
    get=extend_schema(
        summary="Detail d'un device",
        description="Retourne le detail d'un device appartenant a l'utilisateur connecte.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={
            200: DeviceReadSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device introuvable"),
        },
    ),
    put=extend_schema(
        summary="Modifier completement un device",
        description="Met a jour completement un device appartenant a l'utilisateur connecte.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceUpdateSerializer,
        responses={
            200: DeviceReadSerializer,
            400: OpenApiResponse(response=DeviceUpdateValidationErrorResponseSerializer, description="Donnees invalides"),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device introuvable"),
        },
    ),
    patch=extend_schema(
        summary="Modifier partiellement un device",
        description="Met a jour partiellement un device appartenant a l'utilisateur connecte.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceUpdateSerializer,
        responses={
            200: DeviceReadSerializer,
            400: OpenApiResponse(response=DeviceUpdateValidationErrorResponseSerializer, description="Donnees invalides"),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device introuvable"),
        },
    ),
    delete=extend_schema(
        summary="Supprimer un device",
        description="Supprime un device appartenant a l'utilisateur connecte.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={
            204: None,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device introuvable"),
        },
    ),
)
class DeviceDetailApiView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Device.objects.filter(
                application_links__application__owner=self.request.user,
                application_links__is_active=True,
            )
            .distinct()
            .order_by("-id")
        )

    def get_serializer_class(self):
        if self.request.method in ["PATCH", "PUT"]:
            return DeviceUpdateSerializer
        return DeviceReadSerializer

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            raise NotFound("Device introuvable.", code="device_not_found")


class UserOwnedDeviceMixin:
    def get_device(self):
        return (
            Device.objects.filter(
                application_links__application__owner=self.request.user,
                application_links__is_active=True,
                id=self.kwargs["device_id"],
            )
            .distinct()
            .first()
        )


@extend_schema_view(
    get=extend_schema(
        summary="Lister les periodes blanches d'un device",
        description=(
            "Retourne les periodes blanches configurees pour un device. Elles peuvent "
            "etre ponctuelles (`period_type=ONCE`) ou periodiques (`period_type=RECURRING`). "
            "Elles s'appliquent aux deliveries de ce device sans bloquer les autres devices."
        ),
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={200: DeviceQuietPeriodReadSerializer(many=True)},
    ),
    post=extend_schema(
        summary="Creer une periode blanche pour un device",
        description="Ajoute une periode blanche ponctuelle ou periodique sur un device.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceQuietPeriodWriteSerializer,
        examples=[
            OpenApiExample(
                "One-time device quiet period",
                value={
                    "name": "Indisponible ce soir",
                    "period_type": "ONCE",
                    "start_at": "2026-03-27T22:00:00+01:00",
                    "end_at": "2026-03-28T08:00:00+01:00",
                    "is_active": True,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Recurring device quiet period",
                value={
                    "name": "Do not disturb",
                    "period_type": "RECURRING",
                    "recurrence_days": [0, 1, 2, 3, 4],
                    "start_time": "22:00:00",
                    "end_time": "08:00:00",
                    "is_active": True,
                },
                request_only=True,
            ),
        ],
        responses={
            201: DeviceQuietPeriodReadSerializer,
            400: OpenApiResponse(
                response=DeviceQuietPeriodValidationErrorResponseSerializer,
                description="Donnees invalides",
            ),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device introuvable"),
        },
    ),
)
class DeviceQuietPeriodListCreateApiView(UserOwnedDeviceMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        device = self.get_device()
        if device is None:
            return DeviceQuietPeriod.objects.none()
        return device.quiet_periods.all()

    def get_serializer_class(self):
        if self.request.method == "POST":
            return DeviceQuietPeriodWriteSerializer
        return DeviceQuietPeriodReadSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["device"] = self.get_device()
        return context

    def list(self, request, *args, **kwargs):
        if self.get_device() is None:
            raise NotFound("Device introuvable.", code="device_not_found")
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        device = self.get_device()
        if device is None:
            raise NotFound("Device introuvable.", code="device_not_found")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(DeviceQuietPeriodReadSerializer(instance).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary="Detail d'une periode blanche device",
        description="Retourne le detail d'une periode blanche d'un device.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={
            200: DeviceQuietPeriodReadSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Periode blanche introuvable"),
        },
    ),
    patch=extend_schema(
        summary="Modifier une periode blanche device",
        description="Modifie une periode blanche ponctuelle ou periodique d'un device.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceQuietPeriodWriteSerializer,
        responses={
            200: DeviceQuietPeriodReadSerializer,
            400: OpenApiResponse(
                response=DeviceQuietPeriodValidationErrorResponseSerializer,
                description="Donnees invalides",
            ),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Periode blanche introuvable"),
        },
    ),
    delete=extend_schema(
        summary="Supprimer une periode blanche device",
        description="Supprime une periode blanche d'un device.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={
            204: None,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Periode blanche introuvable"),
        },
    ),
)
class DeviceQuietPeriodDetailApiView(UserOwnedDeviceMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "patch", "delete"]

    def get_queryset(self):
        device = self.get_device()
        if device is None:
            return DeviceQuietPeriod.objects.none()
        return device.quiet_periods.all()

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return DeviceQuietPeriodWriteSerializer
        return DeviceQuietPeriodReadSerializer

    def get_object(self):
        device = self.get_device()
        if device is None:
            raise NotFound("Device introuvable.", code="device_not_found")
        return device.quiet_periods.filter(id=self.kwargs["quiet_period_id"]).first()

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            raise NotFound("Periode blanche introuvable.", code="quiet_period_not_found")
        return Response(DeviceQuietPeriodReadSerializer(instance).data)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            raise NotFound("Periode blanche introuvable.", code="quiet_period_not_found")
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(DeviceQuietPeriodReadSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            raise NotFound("Periode blanche introuvable.", code="quiet_period_not_found")
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
