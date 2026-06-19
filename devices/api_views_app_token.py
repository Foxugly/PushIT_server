from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from applications.authentication import get_application_for_raw_app_token
from .models import Device, DeviceApplicationLink, DeviceTokenStatus, UnlinkSource
from .serializers import (
    DetailResponseSerializer,
    DeviceIdentifyResponseSerializer,
    DeviceIdentifySerializer,
    DeviceIdentifyValidationErrorResponseSerializer,
    DeviceLinkWithAppTokenResponseSerializer,
    DeviceLinkWithAppTokenSerializer,
    DeviceLinkWithAppTokenValidationErrorResponseSerializer,
    DeviceUnlinkByApplicationSerializer,
    DeviceUnlinkByApplicationValidationErrorResponseSerializer,
    DeviceUnlinkWithAppTokenResponseSerializer,
    DeviceUnlinkWithAppTokenSerializer,
    DeviceUnlinkWithAppTokenValidationErrorResponseSerializer,
)


def _select_existing_device(push_token):
    """Fetch the device row for ``push_token``, locking it when the backend
    supports row locking so two concurrent upserts for the same token serialise
    instead of racing. ``skip_locked`` is *not* used here: we must operate on the
    row, not skip it, when another tx holds it — the FOR UPDATE simply makes us
    wait for that tx to commit."""
    qs = Device.objects.filter(push_token=push_token)
    if settings.DB_SUPPORTS_ROW_LOCKING:
        qs = qs.select_for_update()
    return qs.first()


def _apply_device_fields(device, *, user, data):
    if device.user_id is not None and device.user_id != user.id:
        device.application_links.filter(is_active=True).update(
            is_active=False,
            unlinked_at=timezone.now(),
            unlink_source=UnlinkSource.TAKEOVER,
        )
    device.user = user
    device.device_name = data.get("device_name", "")
    device.platform = data.get("platform", "android")
    device.push_token_status = DeviceTokenStatus.ACTIVE
    device.last_seen_at = timezone.now()


def _upsert_authenticated_device(*, user, data):
    push_token = data["push_token"]
    # The whole read-modify-write is wrapped in a transaction so the row lock
    # (taken in _select_existing_device) is held for its duration. On backends
    # without row locking (sqlite tests) we additionally guard the INSERT against
    # a concurrent creator via the unique push_token constraint.
    with transaction.atomic():
        device = _select_existing_device(push_token)
        if device is not None:
            _apply_device_fields(device, user=user, data=data)
            device.save(
                update_fields=[
                    "user",
                    "device_name",
                    "platform",
                    "push_token_status",
                    "last_seen_at",
                ]
            )
            return device, False

        device = Device(push_token=push_token)
        _apply_device_fields(device, user=user, data=data)
        try:
            with transaction.atomic():
                device.save()
            return device, True
        except IntegrityError:
            # A concurrent request created the same push_token between our SELECT
            # and INSERT. Re-fetch (locking) and fall through to the update path.
            device = _select_existing_device(push_token)
            if device is None:  # pragma: no cover - lost row, should not happen
                raise
            _apply_device_fields(device, user=user, data=data)
            device.save(
                update_fields=[
                    "user",
                    "device_name",
                    "platform",
                    "push_token_status",
                    "last_seen_at",
                ]
            )
            return device, False


def _serialize_linked_applications(device, request=None):
    links = (
        device.application_links.select_related("application")
        .filter(is_active=True, application__is_active=True)
        .order_by("application__name", "application_id")
    )

    def logo_url(application):
        if not application.logo:
            return None
        url = application.logo.url
        return request.build_absolute_uri(url) if request is not None else url

    return [
        {
            "id": link.application.id,
            "name": link.application.name,
            "description": link.application.description,
            "is_active": link.application.is_active,
            "linked_at": link.linked_at,
            "logo": logo_url(link.application),
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
                "linked_applications": _serialize_linked_applications(device, request),
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

        # get_or_create + the reactivation read-modify-write are wrapped in one
        # transaction. get_or_create is itself race-safe against the link's unique
        # (device, application) constraint; the surrounding lock serialises a
        # concurrent reactivation so it can't clobber the unlink audit fields.
        with transaction.atomic():
            link, link_created = DeviceApplicationLink.objects.get_or_create(
                device=device,
                application=application,
                defaults={"is_active": True},
            )

            if not link_created and not link.is_active:
                # Reactivation: clear the previous unlink audit so an active link
                # never carries stale unlinked_at / unlink_source.
                if settings.DB_SUPPORTS_ROW_LOCKING:
                    link = DeviceApplicationLink.objects.select_for_update().get(pk=link.pk)
                if not link.is_active:
                    link.is_active = True
                    link.unlinked_at = None
                    link.unlink_source = ""
                    link.save(update_fields=["is_active", "unlinked_at", "unlink_source"])

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


@extend_schema_view(
    post=extend_schema(
        summary="Unlink an authenticated device from an application",
        description=(
            "Deactivates the link between the authenticated user's device (identified "
            "by its push token) and the application identified by the submitted app "
            "token. Idempotent: returns unlinked=false when there was no active link "
            "(or the user has no device with that push token)."
        ),
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceUnlinkWithAppTokenSerializer,
        responses={
            200: DeviceUnlinkWithAppTokenResponseSerializer,
            400: OpenApiResponse(
                response=DeviceUnlinkWithAppTokenValidationErrorResponseSerializer,
                description="Invalid data",
            ),
            401: OpenApiResponse(response=DetailResponseSerializer, description="Invalid or missing app token"),
            403: OpenApiResponse(response=DetailResponseSerializer, description="Access denied"),
        },
    )
)
class DeviceUnlinkWithAppTokenApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = DeviceUnlinkWithAppTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        application = get_application_for_raw_app_token(data.get("app_token"))

        device = Device.objects.filter(
            push_token=data["push_token"], user=request.user
        ).first()

        unlinked = False
        if device is not None:
            link = device.application_links.filter(
                application=application, is_active=True
            ).first()
            if link is not None:
                link.is_active = False
                link.unlinked_at = timezone.now()
                link.unlink_source = UnlinkSource.DEVICE_BUTTON
                link.save(update_fields=["is_active", "unlinked_at", "unlink_source"])
                unlinked = True

        return Response(
            {
                "status": "ok",
                "device_id": device.id if device is not None else None,
                "application_id": application.id,
                "unlinked": unlinked,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    post=extend_schema(
        summary="Unlink a device from an application by id",
        description=(
            "Deactivates the link between the authenticated user's device (identified "
            "by its push token) and the application identified by `application_id` — no "
            "app token required. Powers the mobile inbox's per-app unlink. Idempotent: "
            "returns unlinked=false when there was no active link."
        ),
        tags=["Devices"],
        auth=[{"BearerAuth": []}],
        request=DeviceUnlinkByApplicationSerializer,
        responses={
            200: DeviceUnlinkWithAppTokenResponseSerializer,
            400: OpenApiResponse(
                response=DeviceUnlinkByApplicationValidationErrorResponseSerializer,
                description="Invalid data",
            ),
        },
    )
)
class DeviceUnlinkByApplicationApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = DeviceUnlinkByApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        device = Device.objects.filter(
            push_token=data["push_token"], user=request.user
        ).first()

        unlinked = False
        if device is not None:
            link = device.application_links.filter(
                application_id=data["application_id"], is_active=True
            ).first()
            if link is not None:
                link.is_active = False
                link.unlinked_at = timezone.now()
                link.unlink_source = UnlinkSource.INBOX
                link.save(update_fields=["is_active", "unlinked_at", "unlink_source"])
                unlinked = True

        return Response(
            {
                "status": "ok",
                "device_id": device.id if device is not None else None,
                "application_id": data["application_id"],
                "unlinked": unlinked,
            },
            status=status.HTTP_200_OK,
        )
