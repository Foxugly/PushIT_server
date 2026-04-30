from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from applications.authentication import get_application_for_raw_app_token
from .models import Device, DeviceApplicationLink, DeviceTokenStatus
from .serializers import (
    DetailResponseSerializer,
    DeviceIdentifyResponseSerializer,
    DeviceIdentifySerializer,
    DeviceIdentifyValidationErrorResponseSerializer,
    DeviceLinkWithAppTokenResponseSerializer,
    DeviceLinkWithAppTokenSerializer,
    DeviceLinkWithAppTokenValidationErrorResponseSerializer,
)


def _upsert_authenticated_device(*, user, data):
    device = Device.objects.filter(push_token=data["push_token"]).first()
    created = device is None
    if created:
        device = Device(push_token=data["push_token"])
    elif device.user_id is not None and device.user_id != user.id:
        device.application_links.filter(is_active=True).update(is_active=False)

    device.user = user
    device.device_name = data.get("device_name", "")
    device.platform = data.get("platform", "android")
    device.push_token_status = DeviceTokenStatus.ACTIVE
    device.last_seen_at = timezone.now()
    device.save(
        update_fields=[
            "user",
            "device_name",
            "platform",
            "push_token_status",
            "last_seen_at",
        ]
        if not created
        else None
    )
    return device, created


def _serialize_linked_applications(device):
    links = (
        device.application_links.select_related("application")
        .filter(is_active=True, application__is_active=True)
        .order_by("application__name", "application_id")
    )
    return [
        {
            "id": link.application.id,
            "name": link.application.name,
            "description": link.application.description,
            "is_active": link.application.is_active,
            "linked_at": link.linked_at,
        }
        for link in links
    ]


@extend_schema_view(
    post=extend_schema(
        summary="Identify an authenticated device",
        description=(
            "Creates or updates a device for the authenticated user from its push token "
            "and returns the active applications already linked to that device."
        ),
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceIdentifySerializer,
        responses={
            200: DeviceIdentifyResponseSerializer,
            400: OpenApiResponse(
                response=DeviceIdentifyValidationErrorResponseSerializer,
                description="Invalid data",
            ),
            401: OpenApiResponse(response=DetailResponseSerializer, description="Missing or invalid user token"),
        },
    )
)
class DeviceIdentifyApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = DeviceIdentifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device, created = _upsert_authenticated_device(
            user=request.user,
            data=serializer.validated_data,
        )
        return Response(
            {
                "status": "ok",
                "device_id": device.id,
                "device_created": created,
                "linked_applications": _serialize_linked_applications(device),
            },
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    post=extend_schema(
        summary="Link an authenticated device to an application",
        description=(
            "Creates or updates a device for the authenticated user and links it "
            "to the application identified by the submitted app token."
        ),
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceLinkWithAppTokenSerializer,
        responses={
            200: DeviceLinkWithAppTokenResponseSerializer,
            400: OpenApiResponse(
                response=DeviceLinkWithAppTokenValidationErrorResponseSerializer,
                description="Invalid data",
            ),
            401: OpenApiResponse(response=DetailResponseSerializer, description="Invalid or missing app token"),
            403: OpenApiResponse(response=DetailResponseSerializer, description="Access denied"),
        },
    )
)
class DeviceLinkWithAppTokenApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = DeviceLinkWithAppTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        application = get_application_for_raw_app_token(data.get("app_token"))
        device, created = _upsert_authenticated_device(
            user=request.user,
            data=data,
        )

        link, link_created = DeviceApplicationLink.objects.get_or_create(
            device=device,
            application=application,
            defaults={"is_active": True},
        )

        if not link_created and not link.is_active:
            link.is_active = True
            link.save(update_fields=["is_active"])

        return Response(
            {
                "status": "ok",
                "device_id": device.id,
                "device_created": created,
                "link_created": link_created,
                "application_id": application.id,
            },
            status=status.HTTP_200_OK,
        )
