import json
import logging
from pathlib import Path

from django.conf import settings
from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import permissions, serializers
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import ErrorResponseSerializer

logger = logging.getLogger("pushit")

APK_CONTENT_TYPE = "application/vnd.android.package-archive"
# The sidecar is a two-key JSON written by hand; anything bigger is not it.
APK_META_MAX_BYTES = 4096


def _raise_not_found():
    raise NotFound("No Android app is published.", code="android_app_not_found")


class AndroidAppDownloadApiView(APIView):
    """Serve the PushIT Android APK to a signed-in user.

    The file is placed out-of-band by root (``settings.ANDROID_APK_PATH``),
    outside the app tree, so it is never served without authentication.
    nginx buffers the upstream response, so the sync worker is freed as soon
    as the file has been handed to nginx, not when the phone has finished.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="devices_android_app_download",
        summary="Download the Android app (APK)",
        responses={
            (200, APK_CONTENT_TYPE): OpenApiResponse(response=OpenApiTypes.BINARY, description="The APK."),
            404: OpenApiResponse(description="No APK is published."),
        },
    )
    def get(self, request):
        path = settings.ANDROID_APK_PATH
        try:
            handle = open(path, "rb")
        except (FileNotFoundError, IsADirectoryError):
            _raise_not_found()
        return FileResponse(
            handle,
            as_attachment=True,
            filename="pushit.apk",
            content_type=APK_CONTENT_TYPE,
        )


class AndroidAppVersionSerializer(serializers.Serializer):
    version_code = serializers.IntegerField(help_text="Android `versionCode` of the published APK.")
    version_name = serializers.CharField(help_text="Android `versionName` of the published APK, e.g. `1.4.2`.")
    size = serializers.IntegerField(help_text="APK size in bytes.")


def _read_apk_meta(path):
    """Return (version_code, version_name) from the JSON sidecar, or None."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(APK_META_MAX_BYTES + 1)
    except (FileNotFoundError, IsADirectoryError):
        return None
    except OSError as exc:  # e.g. wrong owner/mode on the box
        logger.warning("android_app_meta_unreadable", extra={"error": str(exc)})
        return None
    if len(raw) > APK_META_MAX_BYTES:
        logger.warning("android_app_meta_invalid", extra={"reason": "too_large"})
        return None
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        logger.warning("android_app_meta_invalid", extra={"reason": "not_json"})
        return None
    version_code = data.get("version_code") if isinstance(data, dict) else None
    version_name = data.get("version_name") if isinstance(data, dict) else None
    if (
        not isinstance(version_code, int)
        or isinstance(version_code, bool)
        or version_code < 1
        or not isinstance(version_name, str)
        or not version_name.strip()
    ):
        logger.warning("android_app_meta_invalid", extra={"reason": "bad_fields"})
        return None
    return version_code, version_name.strip()


class AndroidAppVersionApiView(APIView):
    """Version of the published APK, so a client can tell whether it is up to date.

    Read from a JSON sidecar placed by root next to the APK
    (``settings.ANDROID_APK_META_PATH``): ``{"version_code": N, "version_name": "x.y.z"}``.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="devices_android_app_version",
        summary="Version of the published Android app",
        description=(
            "Returns the `versionCode` / `versionName` and size (bytes) of the APK served by "
            "`GET /api/v1/devices/android-app/`. 404 `android_app_not_found` when the APK or "
            "its metadata is missing or invalid."
        ),
        responses={
            200: AndroidAppVersionSerializer,
            404: OpenApiResponse(response=ErrorResponseSerializer, description="No APK is published."),
        },
    )
    def get(self, request):
        apk_path = Path(settings.ANDROID_APK_PATH)
        if not apk_path.is_file():
            _raise_not_found()
        meta = _read_apk_meta(Path(settings.ANDROID_APK_META_PATH))
        if meta is None:
            _raise_not_found()
        try:
            size = apk_path.stat().st_size
        except OSError:
            _raise_not_found()
        version_code, version_name = meta
        return Response(
            AndroidAppVersionSerializer(
                {"version_code": version_code, "version_name": version_name, "size": size}
            ).data
        )
