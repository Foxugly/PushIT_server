from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from applications.authentication import AppTokenAuthentication
from applications.permissions import HasAppToken
from .models import Device, DeviceApplicationLink, DeviceTokenStatus
from .serializers import (
    DetailResponseSerializer,
    DeviceLinkWithAppTokenResponseSerializer,
    DeviceLinkWithAppTokenSerializer,
    DeviceLinkWithAppTokenValidationErrorResponseSerializer,
)


@extend_schema_view(
    post=extend_schema(
        summary="Associer un device a une application via app token",
        description="Cree ou met a jour un device a partir du header `X-App-Token` puis l'associe a l'application.",
        tags=["Devices"],
        request=DeviceLinkWithAppTokenSerializer,
        responses={
            200: DeviceLinkWithAppTokenResponseSerializer,
            400: OpenApiResponse(
                response=DeviceLinkWithAppTokenValidationErrorResponseSerializer,
                description="Donnees invalides",
            ),
            401: OpenApiResponse(response=DetailResponseSerializer, description="App token invalide ou manquant"),
            403: OpenApiResponse(response=DetailResponseSerializer, description="Acces refuse"),
        },
    )
)
class DeviceLinkWithAppTokenApiView(APIView):
    authentication_classes = [AppTokenAuthentication]
    permission_classes = [HasAppToken]

    def post(self, request):
        serializer = DeviceLinkWithAppTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        application = request.auth_application
        device, created = Device.objects.update_or_create(
            push_token=data["push_token"],
            defaults={
                "device_name": data.get("device_name", ""),
                "platform": data.get("platform", "android"),
                "push_token_status": DeviceTokenStatus.ACTIVE,
                "last_seen_at": timezone.now(),
            },
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
