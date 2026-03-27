from drf_spectacular.utils import extend_schema_field, inline_serializer
from django.utils import timezone
from rest_framework import serializers

from applications.models import Application
from config.api_errors import (
    ErrorResponseSerializer as DetailResponseSerializer,
    build_validation_error_serializer,
)
from .models import Notification, NotificationStatus
from .scheduling import compute_effective_scheduled_for

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
    ["application_id", "title", "message", "scheduled_for"],
)

NotificationCreateWithAppTokenValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationCreateWithAppTokenValidationErrorResponse",
    ["title", "message", "scheduled_for"],
)

NotificationFutureUpdateValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationFutureUpdateValidationErrorResponse",
    ["title", "message", "scheduled_for"],
)

NotificationFutureFilterValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationFutureFilterValidationErrorResponse",
    ["effective_scheduled_from", "effective_scheduled_to", "ordering", "has_quiet_period_shift"],
)


class NotificationFutureFilterSerializer(serializers.Serializer):
    effective_scheduled_from = serializers.DateTimeField(required=False)
    effective_scheduled_to = serializers.DateTimeField(required=False)
    has_quiet_period_shift = serializers.BooleanField(required=False)
    ordering = serializers.ChoiceField(
        required=False,
        choices=["effective_scheduled_for", "-effective_scheduled_for"],
    )

    def validate(self, attrs):
        effective_scheduled_from = attrs.get("effective_scheduled_from")
        effective_scheduled_to = attrs.get("effective_scheduled_to")

        if (
            effective_scheduled_from is not None
            and effective_scheduled_to is not None
            and effective_scheduled_to < effective_scheduled_from
        ):
            raise serializers.ValidationError(
                {
                    "effective_scheduled_to": [
                        "La borne de fin doit etre apres ou egale a la borne de debut."
                    ]
                }
            )

        return attrs


NotificationListFilterValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationListFilterValidationErrorResponse",
    [
        "application_id",
        "status",
        "effective_scheduled_from",
        "effective_scheduled_to",
        "has_quiet_period_shift",
        "ordering",
    ],
)


class NotificationListFilterSerializer(NotificationFutureFilterSerializer):
    application_id = serializers.IntegerField(required=False)
    status = serializers.ChoiceField(
        required=False,
        choices=[choice for choice, _ in NotificationStatus.choices],
    )


class NotificationReadSerializer(serializers.ModelSerializer):
    application_id = serializers.IntegerField(source="application.id", read_only=True)
    application_name = serializers.CharField(source="application.name", read_only=True)
    effective_scheduled_for = serializers.SerializerMethodField(
        help_text=(
            "Date effective d'envoi calculee a partir de `scheduled_for` et des "
            "periodes blanches actuellement configurees. Cette valeur peut changer "
            "si les periodes blanches changent, meme sans modifier `scheduled_for`."
        )
    )

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_effective_scheduled_for(self, obj):
        quiet_periods = getattr(obj.application, "_prefetched_objects_cache", {}).get("quiet_periods")
        return compute_effective_scheduled_for(
            obj.application,
            obj.scheduled_for,
            quiet_periods=quiet_periods,
        )

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
            "scheduled_for",
            "effective_scheduled_for",
            "sent_at",
        ]


class BaseNotificationWriteSerializer(serializers.ModelSerializer):
    title = serializers.CharField(max_length=255, trim_whitespace=True)
    message = serializers.CharField(trim_whitespace=True)
    scheduled_for = serializers.DateTimeField(required=False, allow_null=True)

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

    def validate_scheduled_for(self, value):
        if value is None:
            return value
        if value <= timezone.now():
            raise serializers.ValidationError("La date planifiée doit être dans le futur.")
        return value

    @staticmethod
    def build_status_from_scheduled_for(scheduled_for):
        if scheduled_for is not None:
            return NotificationStatus.SCHEDULED
        return NotificationStatus.DRAFT


class NotificationCreateSerializer(BaseNotificationWriteSerializer):
    application_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "application_id",
            "title",
            "message",
            "status",
            "created_at",
            "scheduled_for",
            "sent_at",
        ]
        read_only_fields = ["id", "status", "created_at", "sent_at"]

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
            status=self.build_status_from_scheduled_for(validated_data.get("scheduled_for")),
            scheduled_for=validated_data.get("scheduled_for"),
        )


class NotificationCreateWithAppTokenSerializer(BaseNotificationWriteSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "message",
            "status",
            "created_at",
            "scheduled_for",
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

class NotificationFutureUpdateSerializer(BaseNotificationWriteSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "message",
            "scheduled_for",
            "status",
            "created_at",
            "sent_at",
        ]
        read_only_fields = ["id", "status", "created_at", "sent_at"]

    def update(self, instance, validated_data):
        for field in ["title", "message", "scheduled_for"]:
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        instance.status = NotificationStatus.SCHEDULED
        instance.save(update_fields=["title", "message", "scheduled_for", "status"])
        return instance


class NotificationStatsSerializer(serializers.Serializer):
    status = serializers.CharField()
    count = serializers.IntegerField()
