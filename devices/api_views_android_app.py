from django.conf import settings
from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import permissions
from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

APK_CONTENT_TYPE = "application/vnd.android.package-archive"


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
            raise NotFound("No Android app is published.", code="android_app_not_found")
        return FileResponse(
            handle,
            as_attachment=True,
            filename="pushit.apk",
            content_type=APK_CONTENT_TYPE,
        )
