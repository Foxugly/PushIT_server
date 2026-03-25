from django.http import Http404
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, permissions
from rest_framework.exceptions import NotFound

from .models import Device
from .serializers import (
    DetailResponseSerializer,
    DeviceReadSerializer,
    DeviceUpdateSerializer,
    DeviceUpdateValidationErrorResponseSerializer,
)


@extend_schema_view(
    get=extend_schema(
        summary="Lister les devices",
        description="Retourne la liste des devices appartenant a l'utilisateur connecte.",
        tags=["Devices"],
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
        responses={
            200: DeviceReadSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device introuvable"),
        },
    ),
    put=extend_schema(
        summary="Modifier completement un device",
        description="Met a jour completement un device appartenant a l'utilisateur connecte.",
        tags=["Devices"],
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
