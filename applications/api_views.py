from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import error_response
from .models import Application
from .serializers import (
    ApplicationActivationResponseSerializer,
    ApplicationCreateSerializer,
    ApplicationCreateValidationErrorResponseSerializer,
    ApplicationQuietPeriodReadSerializer,
    ApplicationQuietPeriodValidationErrorResponseSerializer,
    ApplicationQuietPeriodWriteSerializer,
    ApplicationReadSerializer,
    ApplicationRevokeTokenResponseSerializer,
    ApplicationTokenRegenerateResponseSerializer,
    ApplicationUpdateSerializer,
    ApplicationUpdateValidationErrorResponseSerializer,
    DetailResponseSerializer,
)


@extend_schema_view(
    get=extend_schema(
        summary="Lister les applications",
        description="Retourne la liste des applications appartenant a l'utilisateur connecte.",
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
        responses={200: ApplicationReadSerializer(many=True)},
    ),
    post=extend_schema(
        summary="Creer une application",
        description="Cree une nouvelle application pour l'utilisateur connecte.",
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
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
    get=extend_schema(
        summary="Detail d'une application",
        description="Retourne le detail d'une application appartenant a l'utilisateur connecte.",
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
        responses={
            200: ApplicationReadSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Application introuvable"),
        },
    ),
    patch=extend_schema(
        summary="Modifier une application",
        description="Met a jour partiellement le nom ou la description d'une application.",
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
        request=ApplicationUpdateSerializer,
        responses={
            200: ApplicationReadSerializer,
            400: OpenApiResponse(
                response=ApplicationUpdateValidationErrorResponseSerializer,
                description="Donnees invalides",
            ),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Application introuvable"),
        },
    ),
    delete=extend_schema(
        summary="Supprimer une application",
        description="Supprime une application appartenant a l'utilisateur connecte.",
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
        responses={
            204: None,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Application introuvable"),
        },
    ),
)
class ApplicationDetailApiView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "patch", "delete"]

    def get_queryset(self):
        return Application.objects.filter(owner=self.request.user).order_by("-id")

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return ApplicationUpdateSerializer
        return ApplicationReadSerializer

    def get_object(self):
        return self.get_queryset().filter(id=self.kwargs["app_id"]).first()

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            return error_response(
                code="application_not_found",
                detail="Application introuvable.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ApplicationReadSerializer(instance).data)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            return error_response(
                code="application_not_found",
                detail="Application introuvable.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ApplicationReadSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            return error_response(
                code="application_not_found",
                detail="Application introuvable.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    post=extend_schema(
        summary="Regenerer le token d'application",
        description="Genere un nouveau token serveur pour une application de l'utilisateur connecte.",
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
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
        auth=[{"BearerAuth": []}],
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
        auth=[{"BearerAuth": []}],
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
        auth=[{"BearerAuth": []}],
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


class UserOwnedApplicationMixin:
    def get_application(self):
        try:
            return Application.objects.get(id=self.kwargs["app_id"], owner=self.request.user)
        except Application.DoesNotExist:
            return None


@extend_schema_view(
    get=extend_schema(
        summary="Lister les periodes blanches",
        description=(
            "Retourne les periodes blanches configurees pour une application. Une "
            "periode blanche peut etre ponctuelle (`period_type=ONCE`) avec "
            "`start_at` / `end_at`, ou periodique (`period_type=RECURRING`) avec "
            "`recurrence_days`, `start_time` et `end_time`. Les notifications "
            "planifiees sont automatiquement reportees a la fin de la fenetre "
            "active, sans reecriture retroactive de `scheduled_for`."
        ),
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
        responses={200: ApplicationQuietPeriodReadSerializer(many=True)},
    ),
    post=extend_schema(
        summary="Creer une periode blanche",
        description=(
            "Ajoute une periode blanche ponctuelle ou periodique. Si une notification "
            "doit partir pendant cette periode, l'envoi est replanifie a la fin de "
            "la fenetre active. Cette operation ne reecrit pas retroactivement "
            "`scheduled_for`, mais la valeur de lecture `effective_scheduled_for` "
            "des notifications futures tiendra compte des periodes blanches "
            "courantes."
        ),
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
        request=ApplicationQuietPeriodWriteSerializer,
        examples=[
            OpenApiExample(
                "One-time quiet period request",
                value={
                    "name": "Nuit marketing",
                    "period_type": "ONCE",
                    "start_at": "2026-03-27T22:00:00+01:00",
                    "end_at": "2026-03-28T08:00:00+01:00",
                    "recurrence_days": [],
                    "start_time": None,
                    "end_time": None,
                    "is_active": True,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Recurring quiet period request",
                value={
                    "name": "Nuit en semaine",
                    "period_type": "RECURRING",
                    "start_at": None,
                    "end_at": None,
                    "recurrence_days": [0, 1, 2, 3, 4],
                    "start_time": "22:00:00",
                    "end_time": "08:00:00",
                    "is_active": True,
                },
                request_only=True,
            )
        ],
        responses={
            201: ApplicationQuietPeriodReadSerializer,
            400: OpenApiResponse(
                response=ApplicationQuietPeriodValidationErrorResponseSerializer,
                description="Donnees invalides",
                examples=[
                    OpenApiExample(
                        "Validation error",
                        value={
                            "code": "validation_error",
                            "detail": "Validation error.",
                            "errors": {
                                "end_at": [
                                    "La fin de la periode blanche doit etre apres le debut."
                                ]
                            },
                        },
                        response_only=True,
                        status_codes=["400"],
                    )
                ],
            ),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Application introuvable"),
        },
    ),
)
class ApplicationQuietPeriodListCreateApiView(UserOwnedApplicationMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        application = self.get_application()
        if application is None:
            return Application.objects.none()
        return application.quiet_periods.all()

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ApplicationQuietPeriodWriteSerializer
        return ApplicationQuietPeriodReadSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["application"] = self.get_application()
        return context

    def list(self, request, *args, **kwargs):
        if self.get_application() is None:
            return error_response(
                code="application_not_found",
                detail="Application introuvable",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        application = self.get_application()
        if application is None:
            return error_response(
                code="application_not_found",
                detail="Application introuvable",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(
            ApplicationQuietPeriodReadSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(
        summary="Detail d'une periode blanche",
        description="Retourne le detail d'une periode blanche.",
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
        responses={
            200: ApplicationQuietPeriodReadSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Periode blanche introuvable"),
        },
    ),
    patch=extend_schema(
        summary="Modifier une periode blanche",
        description="Modifie une periode blanche ponctuelle ou periodique existante.",
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
        request=ApplicationQuietPeriodWriteSerializer,
        responses={
            200: ApplicationQuietPeriodReadSerializer,
            400: OpenApiResponse(
                response=ApplicationQuietPeriodValidationErrorResponseSerializer,
                description="Donnees invalides",
                examples=[
                    OpenApiExample(
                        "Validation error",
                        value={
                            "code": "validation_error",
                            "detail": "Validation error.",
                            "errors": {
                                "end_at": [
                                    "La fin de la periode blanche doit etre apres le debut."
                                ]
                            },
                        },
                        response_only=True,
                        status_codes=["400"],
                    )
                ],
            ),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Periode blanche introuvable"),
        },
    ),
    delete=extend_schema(
        summary="Supprimer une periode blanche",
        description="Supprime une periode blanche.",
        tags=["Applications"],
        auth=[{"BearerAuth": []}],
        responses={
            204: None,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Periode blanche introuvable"),
        },
    ),
)
class ApplicationQuietPeriodDetailApiView(UserOwnedApplicationMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "patch", "delete"]

    def get_queryset(self):
        application = self.get_application()
        if application is None:
            return Application.objects.none()
        return application.quiet_periods.all()

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return ApplicationQuietPeriodWriteSerializer
        return ApplicationQuietPeriodReadSerializer

    def get_object(self):
        application = self.get_application()
        if application is None:
            raise Application.DoesNotExist
        try:
            return application.quiet_periods.get(id=self.kwargs["quiet_period_id"])
        except application.quiet_periods.model.DoesNotExist:
            return None

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            return error_response(
                code="quiet_period_not_found",
                detail="Periode blanche introuvable.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ApplicationQuietPeriodReadSerializer(instance).data)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            return error_response(
                code="quiet_period_not_found",
                detail="Periode blanche introuvable.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ApplicationQuietPeriodReadSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance is None:
            return error_response(
                code="quiet_period_not_found",
                detail="Periode blanche introuvable.",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
