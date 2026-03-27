from drf_spectacular.utils import inline_serializer
from rest_framework import serializers
from config.api_errors import (
    ErrorResponseSerializer as DetailResponseSerializer,
    build_validation_error_serializer,
)
from .models import Application, ApplicationQuietPeriod


ApplicationCreateValidationErrorResponseSerializer = build_validation_error_serializer(
    "ApplicationCreateValidationErrorResponse",
    ["name", "description"],
)

ApplicationTokenRegenerateResponseSerializer = inline_serializer(
    name="ApplicationTokenRegenerateResponse",
    fields={
        "app_id": serializers.IntegerField(),
        "app_token_prefix": serializers.CharField(),
        "new_app_token": serializers.CharField(),
    },
)

ApplicationActivationResponseSerializer = inline_serializer(
    name="ApplicationActivationResponse",
    fields={
        "app_id": serializers.IntegerField(),
        "is_active": serializers.BooleanField(),
    },
)

ApplicationRevokeTokenResponseSerializer = inline_serializer(
    name="ApplicationRevokeTokenResponse",
    fields={
        "app_id": serializers.IntegerField(),
        "revoked_at": serializers.DateTimeField(allow_null=True),
    },
)

ApplicationQuietPeriodValidationErrorResponseSerializer = build_validation_error_serializer(
    "ApplicationQuietPeriodValidationErrorResponse",
    ["name", "start_at", "end_at"],
)

class ApplicationReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Application
        fields = [
            "id",
            "name",
            "description",
            "app_token_prefix",
            "is_active",
            "revoked_at",
            "last_used_at",
            "created_at",
        ]

class ApplicationCreateSerializer(serializers.ModelSerializer):
    app_token = serializers.CharField(read_only=True)

    class Meta:
        model = Application
        fields = [
            "id",
            "name",
            "description",
            "app_token_prefix",
            "app_token",
            "is_active",
            "revoked_at",
            "last_used_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "app_token_prefix",
            "app_token",
            "is_active",
            "revoked_at",
            "last_used_at",
            "created_at",
        ]

    def create(self, validated_data):
        app = Application(
            owner=self.context["request"].user,
            name=validated_data["name"],
            description=validated_data.get("description", ""),
        )
        raw_token = app.set_new_app_token()
        app.save()
        app._raw_app_token = raw_token
        return app

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["app_token"] = getattr(instance, "_raw_app_token", None)
        return data


class ApplicationQuietPeriodReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApplicationQuietPeriod
        fields = [
            "id",
            "application",
            "name",
            "start_at",
            "end_at",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ApplicationQuietPeriodWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApplicationQuietPeriod
        fields = [
            "id",
            "name",
            "start_at",
            "end_at",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        start_at = attrs.get("start_at", getattr(instance, "start_at", None))
        end_at = attrs.get("end_at", getattr(instance, "end_at", None))

        if start_at is None or end_at is None:
            return attrs

        if end_at <= start_at:
            raise serializers.ValidationError(
                {"end_at": ["La fin de la période blanche doit être après le début."]}
            )

        return attrs

    def create(self, validated_data):
        return ApplicationQuietPeriod.objects.create(
            application=self.context["application"],
            **validated_data,
        )
