from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from applications.authentication import get_application_for_enrolment
from .models import Device, DeviceApplicationLink, DeviceQuietPeriod, DeviceTokenStatus, UnlinkSource
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
    # The app just proved this token is live: a device previously invalidated
    # (FCM said "unregistered", or it was revoked) is fully reactivated, not
    # left half-dead with is_active=False / a stale failure counter.
    device.push_token_status = DeviceTokenStatus.ACTIVE
    device.is_active = True
    device.invalidated_at = None
    device.invalidation_reason = ""
    device.failure_count = 0
    device.last_seen_at = timezone.now()


_DEVICE_UPSERT_FIELDS = [
    "user",
    "device_name",
    "platform",
    "push_token_status",
    "is_active",
    "invalidated_at",
    "invalidation_reason",
    "failure_count",
    "last_seen_at",
]


def _merge_device_into(source, target):
    """Move `source`'s links, inbox history and quiet periods onto `target`,
    then delete `source`. Both belong to the same user (checked by the caller).

    A link to an application `target` already has is not duplicated: `target`'s
    row wins, reactivated if only `source`'s was active. A delivery of a
    notification `target` already has is dropped with `source`.
    """
    from notifications.models import NotificationDelivery

    target_links = {link.application_id: link for link in target.application_links.all()}
    for link in source.application_links.all():
        existing = target_links.get(link.application_id)
        if existing is None:
            link.device = target
            link.save(update_fields=["device"])
        elif link.is_active and not existing.is_active:
            existing.is_active = True
            existing.unlinked_at = None
            existing.unlink_source = ""
            existing.save(update_fields=["is_active", "unlinked_at", "unlink_source"])

    already = target.deliveries.values_list("notification_id", flat=True)
    NotificationDelivery.objects.filter(device=source).exclude(notification_id__in=already).update(
        device=target
    )
    DeviceQuietPeriod.objects.filter(device=source).update(device=target)
    source.delete()


def _rotate_push_token(*, user, data):
    """FCM token rotation (`previous_push_token`). Returns the device or None.

    Only a device that already belongs to the authenticated user is rotated —
    a previous token that is unknown, unowned or another user's is ignored and
    the caller falls back to the plain upsert. When a row for the new token
    already exists (an older client identified with it first), the *old*
    device is kept, because it carries the links and the inbox history the
    user knows, and the new row is merged into it — unless that new row is
    another user's device, which is never merged (plain upsert/takeover then).
    """
    previous = (data.get("previous_push_token") or "").strip()
    push_token = data["push_token"]
    if not previous or previous == push_token:
        return None

    old = _select_existing_device(previous)
    if old is None or old.user_id != user.id:
        return None

    clash = _select_existing_device(push_token)
    if clash is not None:
        if clash.user_id not in (None, user.id):
            return None
        _merge_device_into(clash, old)

    old.push_token = push_token
    _apply_device_fields(old, user=user, data=data)
    old.save(update_fields=["push_token", *_DEVICE_UPSERT_FIELDS])
    return old


def _upsert_authenticated_device(*, user, data):
    push_token = data["push_token"]
    # The whole read-modify-write is wrapped in a transaction so the row lock
    # (taken in _select_existing_device) is held for its duration. On backends
    # without row locking (sqlite tests) we additionally guard the INSERT against
    # a concurrent creator via the unique push_token constraint.
    with transaction.atomic():
        rotated = _rotate_push_token(user=user, data=data)
        if rotated is not None:
            return rotated, False

        device = _select_existing_device(push_token)
        if device is not None:
            _apply_device_fields(device, user=user, data=data)
            device.save(update_fields=_DEVICE_UPSERT_FIELDS)
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
            device.save(update_fields=_DEVICE_UPSERT_FIELDS)
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
            "and returns the active applications already linked to that device. After "
            "an FCM token rotation, send the old token as `previous_push_token`: if it "
            "is one of the caller's devices, that device is updated in place (links and "
            "inbox kept, same `device_id`). Unknown/foreign previous tokens are ignored."
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
            401: OpenApiResponse(response=DetailResponseSerializer, description="Missing or invalid user JWT"),
            403: OpenApiResponse(
                response=DetailResponseSerializer,
                description=(
                    "Enrolment code refused (valid JWT): code is one of app_token_missing, "
                    "app_token_invalid, app_token_invalid_format, app_token_inactive, "
                    "app_token_revoked"
                ),
            ),
        },
    )
)
class DeviceLinkWithAppTokenApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = DeviceLinkWithAppTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        application = get_application_for_enrolment(data.get("app_token"))
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
            401: OpenApiResponse(response=DetailResponseSerializer, description="Missing or invalid user JWT"),
            403: OpenApiResponse(
                response=DetailResponseSerializer,
                description=(
                    "Enrolment code refused (valid JWT): code is one of app_token_missing, "
                    "app_token_invalid, app_token_invalid_format, app_token_inactive, "
                    "app_token_revoked"
                ),
            ),
        },
    )
)
class DeviceUnlinkWithAppTokenApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = DeviceUnlinkWithAppTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        application = get_application_for_enrolment(data.get("app_token"))

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
