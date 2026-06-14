from django.http import Http404
from django.db.models import Prefetch, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics, permissions, serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from config.pagination import OptionalPageNumberPagination
from notifications.models import Notification, NotificationDelivery
from notifications.serializers import DeviceNotificationSerializer

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
        summary="List devices",
        description="Returns the list of devices owned by the authenticated user.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={200: DeviceReadSerializer(many=True)},
    ),
)
class DeviceListApiView(generics.ListAPIView):
    serializer_class = DeviceReadSerializer
    permission_classes = [permissions.IsAuthenticated]
    # Bare array by default; paginates only on ?page / ?page_size (cheap counts +
    # lazy tables). Linked recipient devices can grow unbounded.
    pagination_class = OptionalPageNumberPagination

    def get_queryset(self):
        return (
            Device.objects.filter(
                Q(user=self.request.user)
                | Q(
                    application_links__application__owner=self.request.user,
                    application_links__is_active=True,
                )
            )
            .distinct()
            .prefetch_related("application_links")
            .order_by("-id")
        )


@extend_schema_view(
    get=extend_schema(
        summary="Device detail",
        description="Returns the detail of a device owned by the authenticated user.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={
            200: DeviceReadSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device not found"),
        },
    ),
    put=extend_schema(
        summary="Fully update a device",
        description="Fully updates a device owned by the authenticated user.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceUpdateSerializer,
        responses={
            200: DeviceReadSerializer,
            400: OpenApiResponse(response=DeviceUpdateValidationErrorResponseSerializer, description="Invalid data"),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device not found"),
        },
    ),
    patch=extend_schema(
        summary="Partially update a device",
        description="Partially updates a device owned by the authenticated user.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceUpdateSerializer,
        responses={
            200: DeviceReadSerializer,
            400: OpenApiResponse(response=DeviceUpdateValidationErrorResponseSerializer, description="Invalid data"),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device not found"),
        },
    ),
    delete=extend_schema(
        summary="Delete a device",
        description="Deletes a device owned by the authenticated user.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={
            204: None,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device not found"),
        },
    ),
)
class DeviceDetailApiView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Device.objects.filter(
                Q(user=self.request.user)
                | Q(
                    application_links__application__owner=self.request.user,
                    application_links__is_active=True,
                )
            )
            .distinct()
            .prefetch_related("application_links")
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
            raise NotFound("Device not found.", code="device_not_found")


class UserOwnedDeviceMixin:
    def get_device(self):
        return (
            Device.objects.filter(
                Q(user=self.request.user)
                | Q(
                    application_links__application__owner=self.request.user,
                    application_links__is_active=True,
                ),
                id=self.kwargs["device_id"],
            )
            .distinct()
            .first()
        )


@extend_schema_view(
    get=extend_schema(
        summary="Notifications delivered to a device",
        description=(
            "Paginated list of notifications delivered to the given device, "
            "restricted to applications owned by the authenticated user, with this "
            "device's delivery outcome (`delivery_status`, `delivery_sent_at`). "
            "Optionally filtered by `application_id`."
        ),
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        parameters=[
            OpenApiParameter(
                name="application_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Only notifications of this application (must be owned by the caller).",
            ),
        ],
        responses={
            200: DeviceNotificationSerializer(many=True),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device not found"),
        },
    ),
)
class DeviceNotificationsApiView(UserOwnedDeviceMixin, generics.ListAPIView):
    """Owner-facing reverse view: every notification of the caller's apps that was
    delivered to this device. Paginated (default PageNumberPagination)."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = DeviceNotificationSerializer

    def get_device(self):
        # Cache so list()/get_queryset()/get_serializer_context() don't re-query.
        if not hasattr(self, "_device_cache"):
            self._device_cache = super().get_device()
        return self._device_cache

    def get_queryset(self):
        device = self.get_device()
        if device is None:
            return Notification.objects.none()
        queryset = (
            Notification.objects.filter(
                application__owner=self.request.user,
                deliveries__device=device,
            )
            .select_related("application")
            .prefetch_related(
                Prefetch(
                    "deliveries",
                    queryset=NotificationDelivery.objects.filter(device=device),
                    to_attr="device_deliveries",
                )
            )
            .distinct()
            .order_by("-id")
        )
        application_id = self.request.query_params.get("application_id")
        if application_id:
            try:
                queryset = queryset.filter(application_id=int(application_id))
            except (TypeError, ValueError):
                raise serializers.ValidationError({"application_id": "Must be an integer."})
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        device = self.get_device()
        context["device_id"] = device.id if device else None
        return context

    def list(self, request, *args, **kwargs):
        if self.get_device() is None:
            raise NotFound("Device not found.", code="device_not_found")
        return super().list(request, *args, **kwargs)


@extend_schema_view(
    get=extend_schema(
        summary="List device quiet periods",
        description=(
            "Returns the quiet periods configured for a device. They can be "
            "one-time (`period_type=ONCE`) or recurring (`period_type=RECURRING`). "
            "They apply to deliveries for this device without blocking other devices."
        ),
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={200: DeviceQuietPeriodReadSerializer(many=True)},
    ),
    post=extend_schema(
        summary="Create a device quiet period",
        description="Adds a one-time or recurring quiet period on a device.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceQuietPeriodWriteSerializer,
        examples=[
            OpenApiExample(
                "One-time device quiet period",
                value={
                    "name": "Unavailable tonight",
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
                description="Invalid data",
            ),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Device not found"),
        },
    ),
)
class DeviceQuietPeriodListCreateApiView(UserOwnedDeviceMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    # SPA reads this as a bare array — disable global pagination (see /apps/).
    pagination_class = None

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
            raise NotFound("Device not found.", code="device_not_found")
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        device = self.get_device()
        if device is None:
            raise NotFound("Device not found.", code="device_not_found")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(DeviceQuietPeriodReadSerializer(instance).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary="Device quiet period detail",
        description="Returns the detail of a device quiet period.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={
            200: DeviceQuietPeriodReadSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Quiet period not found"),
        },
    ),
    patch=extend_schema(
        summary="Update a device quiet period",
        description="Updates a one-time or recurring device quiet period.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceQuietPeriodWriteSerializer,
        responses={
            200: DeviceQuietPeriodReadSerializer,
            400: OpenApiResponse(
                response=DeviceQuietPeriodValidationErrorResponseSerializer,
                description="Invalid data",
            ),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Quiet period not found"),
        },
    ),
    delete=extend_schema(
        summary="Delete a device quiet period",
        description="Deletes a device quiet period.",
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        responses={
            204: None,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Quiet period not found"),
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
            raise NotFound("Device not found.", code="device_not_found")
        return device.quiet_periods.filter(id=self.kwargs["quiet_period_id"]).first()

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            raise NotFound("Quiet period not found.", code="quiet_period_not_found")
        return Response(DeviceQuietPeriodReadSerializer(instance).data)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            raise NotFound("Quiet period not found.", code="quiet_period_not_found")
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(DeviceQuietPeriodReadSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            raise NotFound("Quiet period not found.", code="quiet_period_not_found")
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
