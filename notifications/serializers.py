from drf_spectacular.utils import inline_serializer
from rest_framework import serializers

from applications.models import Application
from config.api_errors import (
    ErrorResponseSerializer as DetailResponseSerializer,
    build_validation_error_serializer,
)
from .models import Notification, NotificationStatus

NotificationQueuedResponseSerializer = inline_serializer(
    name="NotificationQueuedResponse",
    fields={
        "status": serializers.CharField(),
        "notification_id": serializers.IntegerField(),
        "task_id": serializers.CharField(),
    },
)

NotificationCreateValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationCreateValidationErrorResponse",
    ["application_id", "title", "message"],
)

NotificationCreateWithAppTokenValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationCreateWithAppTokenValidationErrorResponse",
    ["title", "message"],
)


class NotificationReadSerializer(serializers.ModelSerializer):
    application_id = serializers.IntegerField(source="application.id", read_only=True)
    application_name = serializers.CharField(source="application.name", read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "application_id",
            "application_name",
            "title",
            "message",
            "status",
            "created_at",
            "sent_at",
        ]


class NotificationCreateSerializer(serializers.ModelSerializer):
    application_id = serializers.IntegerField(write_only=True)
    title = serializers.CharField(max_length=255, trim_whitespace=True)
    message = serializers.CharField(trim_whitespace=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "application_id",
            "title",
            "message",
            "status",
            "created_at",
            "sent_at",
        ]
        read_only_fields = ["id", "status", "created_at", "sent_at"]

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError("Le titre ne peut pas être vide.")
        return value

    def validate_message(self, value):
        if not value.strip():
            raise serializers.ValidationError("Le message ne peut pas être vide.")
        if len(value.strip()) > 5000:
            raise serializers.ValidationError("Le message est trop long.")
        return value

    def validate_application_id(self, value):
        request = self.context["request"]
        try:
            app = Application.objects.get(id=value, owner=request.user)
        except Application.DoesNotExist:
            raise serializers.ValidationError("Application introuvable.")
        self.context["application"] = app
        return value

    def create(self, validated_data):
        validated_data.pop("application_id")
        application = self.context["application"]
        return Notification.objects.create(
            application=application,
            title=validated_data["title"],
            message=validated_data["message"],
            status=NotificationStatus.DRAFT,
        )


class NotificationCreateWithAppTokenSerializer(serializers.ModelSerializer):
    title = serializers.CharField(max_length=255, trim_whitespace=True)
    message = serializers.CharField(trim_whitespace=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "message",
            "status",
            "created_at",
            "sent_at",
        ]
        read_only_fields = ["id", "status", "created_at", "sent_at"]

    def create(self, validated_data):
        """
        La création réelle est gérée dans :
        notifications/api_views_app_token.py
        -> NotificationCreateWithAppTokenApiView.post()

        Ce serializer reste responsable de la validation.
        """
        raise NotImplementedError(
            "La création doit être effectuée dans NotificationCreateWithAppTokenApiView.post()."
        )

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError("Le titre ne peut pas être vide.")
        return value

    def validate_message(self, value):
        if not value.strip():
            raise serializers.ValidationError("Le message ne peut pas être vide.")
        if len(value.strip()) > 5000:
            raise serializers.ValidationError("Le message est trop long.")
        return value


class NotificationStatsSerializer(serializers.Serializer):
    status = serializers.CharField()
    count = serializers.IntegerField()
