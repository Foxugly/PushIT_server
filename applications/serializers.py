from drf_spectacular.utils import inline_serializer
from rest_framework import serializers

from config.api_errors import (
    ErrorResponseSerializer as DetailResponseSerializer,
    build_validation_error_serializer,
)
from .models import Application, ApplicationQuietPeriod, QuietPeriodType


ApplicationCreateValidationErrorResponseSerializer = build_validation_error_serializer(
    "ApplicationCreateValidationErrorResponse",
    ["name", "description"],
)

ApplicationUpdateValidationErrorResponseSerializer = build_validation_error_serializer(
    "ApplicationUpdateValidationErrorResponse",
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
    ["name", "period_type", "start_at", "end_at", "recurrence_days", "start_time", "end_time"],
)


class ApplicationReadSerializer(serializers.ModelSerializer):
    inbound_email_address = serializers.CharField(read_only=True)

    class Meta:
        model = Application
        fields = [
            "id",
            "name",
            "description",
            "app_token_prefix",
            "inbound_email_alias",
            "inbound_email_address",
            "is_active",
            "revoked_at",
            "last_used_at",
            "created_at",
        ]


class ApplicationCreateSerializer(serializers.ModelSerializer):
    app_token = serializers.CharField(read_only=True)
    inbound_email_address = serializers.CharField(read_only=True)

    class Meta:
        model = Application
        fields = [
            "id",
            "name",
            "description",
            "app_token_prefix",
            "inbound_email_alias",
            "inbound_email_address",
            "app_token",
            "is_active",
            "revoked_at",
            "last_used_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "app_token_prefix",
            "inbound_email_alias",
            "inbound_email_address",
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


class ApplicationUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Application
        fields = ["name", "description"]


class ApplicationQuietPeriodReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApplicationQuietPeriod
        fields = [
            "id",
            "application",
            "name",
            "period_type",
            "start_at",
            "end_at",
            "recurrence_days",
            "start_time",
            "end_time",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ApplicationQuietPeriodWriteSerializer(serializers.ModelSerializer):
    period_type = serializers.ChoiceField(choices=QuietPeriodType.choices, required=False)
    recurrence_days = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        required=False,
    )

    class Meta:
        model = ApplicationQuietPeriod
        fields = [
            "id",
            "name",
            "period_type",
            "start_at",
            "end_at",
            "recurrence_days",
            "start_time",
            "end_time",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        period_type = attrs.get("period_type", getattr(instance, "period_type", QuietPeriodType.ONCE))
        changing_period_type = instance is not None and "period_type" in attrs and period_type != instance.period_type
        start_at = attrs.get("start_at", None if changing_period_type else getattr(instance, "start_at", None))
        end_at = attrs.get("end_at", None if changing_period_type else getattr(instance, "end_at", None))
        recurrence_days = attrs.get(
            "recurrence_days",
            [] if changing_period_type else getattr(instance, "recurrence_days", []),
        )
        start_time = attrs.get("start_time", None if changing_period_type else getattr(instance, "start_time", None))
        end_time = attrs.get("end_time", None if changing_period_type else getattr(instance, "end_time", None))

        errors = {}

        if period_type == QuietPeriodType.ONCE:
            if start_at is None:
                errors["start_at"] = ["Ce champ est obligatoire pour une periode ponctuelle."]
            if end_at is None:
                errors["end_at"] = ["Ce champ est obligatoire pour une periode ponctuelle."]
            if start_at is not None and end_at is not None and end_at <= start_at:
                errors["end_at"] = ["La fin de la periode blanche doit etre apres le debut."]
            if recurrence_days:
                errors["recurrence_days"] = ["Ce champ doit etre vide pour une periode ponctuelle."]
            if start_time is not None:
                errors["start_time"] = ["Ce champ doit etre nul pour une periode ponctuelle."]
            if end_time is not None:
                errors["end_time"] = ["Ce champ doit etre nul pour une periode ponctuelle."]
        else:
            if not recurrence_days:
                errors["recurrence_days"] = ["Au moins un jour de recurrence est obligatoire."]
            if start_time is None:
                errors["start_time"] = ["Ce champ est obligatoire pour une periode periodique."]
            if end_time is None:
                errors["end_time"] = ["Ce champ est obligatoire pour une periode periodique."]
            if start_time is not None and end_time is not None and start_time == end_time:
                errors["end_time"] = ["L'heure de fin doit etre differente de l'heure de debut."]
            if start_at is not None:
                errors["start_at"] = ["Ce champ doit etre nul pour une periode periodique."]
            if end_at is not None:
                errors["end_at"] = ["Ce champ doit etre nul pour une periode periodique."]

        if errors:
            raise serializers.ValidationError(errors)

        if recurrence_days:
            attrs["recurrence_days"] = sorted(set(recurrence_days))

        return attrs

    def _normalize_payload(self, validated_data, instance=None):
        period_type = validated_data.get(
            "period_type",
            getattr(instance, "period_type", QuietPeriodType.ONCE),
        )
        if period_type == QuietPeriodType.ONCE:
            validated_data["recurrence_days"] = []
            validated_data["start_time"] = None
            validated_data["end_time"] = None
        else:
            validated_data["start_at"] = None
            validated_data["end_at"] = None
        return validated_data

    def create(self, validated_data):
        validated_data = self._normalize_payload(validated_data)
        return ApplicationQuietPeriod.objects.create(
            application=self.context["application"],
            **validated_data,
        )

    def update(self, instance, validated_data):
        validated_data = self._normalize_payload(validated_data, instance=instance)
        return super().update(instance, validated_data)
