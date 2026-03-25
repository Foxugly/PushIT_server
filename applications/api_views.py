from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import error_response
from .models import Application
from .serializers import (
    ApplicationActivationResponseSerializer,
    ApplicationCreateSerializer,
    ApplicationCreateValidationErrorResponseSerializer,
    ApplicationReadSerializer,
    ApplicationRevokeTokenResponseSerializer,
    ApplicationTokenRegenerateResponseSerializer,
    DetailResponseSerializer,
)


@extend_schema_view(
    get=extend_schema(
        summary="Lister les applications",
        description="Retourne la liste des applications appartenant a l'utilisateur connecte.",
        tags=["Applications"],
        responses={200: ApplicationReadSerializer(many=True)},
    ),
    post=extend_schema(
        summary="Creer une application",
        description="Cree une nouvelle application pour l'utilisateur connecte.",
        tags=["Applications"],
        request=ApplicationCreateSerializer,
        responses={
            201: ApplicationReadSerializer,
            400: OpenApiResponse(
                response=ApplicationCreateValidationErrorResponseSerializer,
                description="Donnees invalides",
            ),
        },
    ),
)
class ApplicationListCreateApiView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Application.objects.filter(owner=self.request.user).order_by("-id")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ApplicationCreateSerializer
        return ApplicationReadSerializer

    def get_serializer_context(self):
        return {"request": self.request}

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        response_serializer = ApplicationReadSerializer(
            instance,
            context=self.get_serializer_context(),
        )
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    post=extend_schema(
        summary="Regenerer le token d'application",
        description="Genere un nouveau token serveur pour une application de l'utilisateur connecte.",
        tags=["Applications"],
        request=None,
        responses={
            200: ApplicationTokenRegenerateResponseSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Application introuvable"),
        },
    )
)
class ApplicationRegenerateTokenApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, app_id):
        try:
            app = Application.objects.get(id=app_id, owner=request.user)
        except Application.DoesNotExist:
            return error_response(code="application_not_found", detail="Application introuvable", http_status=status.HTTP_404_NOT_FOUND)

        raw_token = app.set_new_app_token()
        app.save(update_fields=["app_token_prefix", "app_token_hash", "revoked_at", "last_used_at"])
        return Response(
            {"app_id": app.id, "app_token_prefix": app.app_token_prefix, "new_app_token": raw_token},
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    post=extend_schema(
        summary="Activer une application",
        description="Active une application appartenant a l'utilisateur connecte.",
        tags=["Applications"],
        request=None,
        responses={
            200: OpenApiResponse(response=ApplicationActivationResponseSerializer, description="Application activee"),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Application introuvable"),
        },
    )
)
class ApplicationActivateApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, app_id):
        try:
            app = Application.objects.get(id=app_id, owner=request.user)
        except Application.DoesNotExist:
            return error_response(code="application_not_found", detail="Application introuvable", http_status=status.HTTP_404_NOT_FOUND)

        if not app.is_active:
            app.is_active = True
            app.save(update_fields=["is_active"])
        return Response({"app_id": app.id, "is_active": app.is_active}, status=status.HTTP_200_OK)


@extend_schema_view(
    post=extend_schema(
        summary="Desactiver une application",
        description="Desactive une application appartenant a l'utilisateur connecte.",
        tags=["Applications"],
        request=None,
        responses={
            200: OpenApiResponse(response=ApplicationActivationResponseSerializer, description="Application desactivee"),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Application introuvable"),
        },
    )
)
class ApplicationDeactivateApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, app_id):
        try:
            app = Application.objects.get(id=app_id, owner=request.user)
        except Application.DoesNotExist:
            return error_response(code="application_not_found", detail="Application introuvable", http_status=status.HTTP_404_NOT_FOUND)

        if app.is_active:
            app.is_active = False
            app.save(update_fields=["is_active"])
        return Response({"app_id": app.id, "is_active": app.is_active}, status=status.HTTP_200_OK)


@extend_schema_view(
    post=extend_schema(
        summary="Revoquer le token d'application",
        description="Revoque le token actuel de l'application.",
        tags=["Applications"],
        request=None,
        responses={
            200: ApplicationRevokeTokenResponseSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Application introuvable"),
        },
    )
)
class ApplicationRevokeTokenApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, app_id):
        try:
            app = Application.objects.get(id=app_id, owner=request.user)
        except Application.DoesNotExist:
            return error_response(code="application_not_found", detail="Application introuvable", http_status=status.HTTP_404_NOT_FOUND)
        app.revoke_token()
        return Response({"app_id": app.id, "revoked_at": app.revoked_at}, status=status.HTTP_200_OK)
