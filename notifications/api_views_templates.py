from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from applications.models import Application
from .models import NotificationTemplate
from .serializers import (
    DetailResponseSerializer,
    NotificationTemplateReadSerializer,
    NotificationTemplateWriteSerializer,
    NotificationTemplateValidationErrorResponseSerializer,
)


class UserOwnedApplicationTemplateMixin:
    def get_application(self):
        try:
            return Application.objects.get(
                id=self.kwargs["app_id"],
                owner=self.request.user,
            )
        except Application.DoesNotExist:
            return None


@extend_schema_view(
    get=extend_schema(
        summary="List notification templates",
        description="Returns the notification templates for an application owned by the authenticated user.",
        tags=["Notification Templates"],
        auth=[{"BearerAuth": []}],
        responses={
            200: NotificationTemplateReadSerializer(many=True),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Application not found"),
        },
    ),
    post=extend_schema(
        summary="Create a notification template",
        description=(
            "Creates a notification template for an application. "
            "Use `{{variable_name}}` placeholders in title_template and message_template. "
            "These will be replaced by the variables dict when creating a notification from the template."
        ),
        tags=["Notification Templates"],
        auth=[{"BearerAuth": []}],
        request=NotificationTemplateWriteSerializer,
        examples=[
            OpenApiExample(
                "Welcome template",
                value={
                    "name": "welcome",
                    "title_template": "Welcome {{username}}",
                    "message_template": "Hello {{username}}, welcome to {{app_name}}!",
                },
                request_only=True,
            ),
        ],
        responses={
            201: NotificationTemplateReadSerializer,
            400: OpenApiResponse(
                response=NotificationTemplateValidationErrorResponseSerializer,
                description="Invalid data",
            ),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Application not found"),
        },
    ),
)
class NotificationTemplateListCreateApiView(
    UserOwnedApplicationTemplateMixin, generics.ListCreateAPIView,
):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        application = self.get_application()
        if application is None:
            return NotificationTemplate.objects.none()
        return application.notification_templates.all()

    def get_serializer_class(self):
        if self.request.method == "POST":
            return NotificationTemplateWriteSerializer
        return NotificationTemplateReadSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["application"] = self.get_application()
        return context

    def list(self, request, *args, **kwargs):
        if self.get_application() is None:
            raise NotFound("Application not found.", code="application_not_found")
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        if self.get_application() is None:
            raise NotFound("Application not found.", code="application_not_found")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(
            NotificationTemplateReadSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(
        summary="Notification template detail",
        description="Returns the detail of a notification template.",
        tags=["Notification Templates"],
        auth=[{"BearerAuth": []}],
        responses={
            200: NotificationTemplateReadSerializer,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Template not found"),
        },
    ),
    patch=extend_schema(
        summary="Update a notification template",
        description="Updates an existing notification template.",
        tags=["Notification Templates"],
        auth=[{"BearerAuth": []}],
        request=NotificationTemplateWriteSerializer,
        responses={
            200: NotificationTemplateReadSerializer,
            400: OpenApiResponse(
                response=NotificationTemplateValidationErrorResponseSerializer,
                description="Invalid data",
            ),
            404: OpenApiResponse(response=DetailResponseSerializer, description="Template not found"),
        },
    ),
    delete=extend_schema(
        summary="Delete a notification template",
        description="Deletes a notification template.",
        tags=["Notification Templates"],
        auth=[{"BearerAuth": []}],
        responses={
            204: None,
            404: OpenApiResponse(response=DetailResponseSerializer, description="Template not found"),
        },
    ),
)
class NotificationTemplateDetailApiView(
    UserOwnedApplicationTemplateMixin, generics.RetrieveUpdateDestroyAPIView,
):
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "patch", "delete"]

    def get_queryset(self):
        application = self.get_application()
        if application is None:
            return NotificationTemplate.objects.none()
        return application.notification_templates.all()

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return NotificationTemplateWriteSerializer
        return NotificationTemplateReadSerializer

    def get_object(self):
        application = self.get_application()
        if application is None:
            raise NotFound("Application not found.", code="application_not_found")
        instance = application.notification_templates.filter(
            id=self.kwargs["template_id"],
        ).first()
        if instance is None:
            raise NotFound("Template not found.", code="template_not_found")
        return instance

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(NotificationTemplateReadSerializer(instance).data)
