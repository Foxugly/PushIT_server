from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers
from config.api_errors import (
    ErrorResponseSerializer as DetailResponseSerializer,
    build_validation_error_serializer,
)
from applications.models import QuietPeriodType
from .models import Device, DevicePlatform, DeviceQuietPeriod

DeviceLinkWithAppTokenResponseSerializer = inline_serializer(
    name="DeviceLinkWithAppTokenResponse",
    fields={
        "status": serializers.CharField(),
        "device_id": serializers.IntegerField(),
        "device_created": serializers.BooleanField(),
        "link_created": serializers.BooleanField(),
        "application_id": serializers.IntegerField(),
    },
)

DeviceUpdateValidationErrorResponseSerializer = build_validation_error_serializer(
    "DeviceUpdateValidationErrorResponse",
    ["device_name", "platform", "push_token_status"],
)

DeviceLinkWithAppTokenValidationErrorResponseSerializer = build_validation_error_serializer(
    "DeviceLinkWithAppTokenValidationErrorResponse",
    ["device_name", "platform", "push_token"],
)

DeviceQuietPeriodValidationErrorResponseSerializer = build_validation_error_serializer(
    "DeviceQuietPeriodValidationErrorResponse",
    ["name", "period_type", "start_at", "end_at", "recurrence_days", "start_time", "end_time"],
)

class DeviceReadSerializer(serializers.ModelSerializer):
    application_ids = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = [
            "id",
            "device_name",
            "platform",
            "push_token_status",
            "last_seen_at",
            "created_at",
            "application_ids",
        ]

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_application_ids(self, obj) -> list[int]:
        return list(
            obj.application_links.filter(is_active=True)
            .values_list("application_id", flat=True)
        )


class DeviceUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = [
            "device_name",
            "platform",
            "push_token_status",
        ]

class DeviceLinkWithAppTokenSerializer(serializers.Serializer):
    device_name = serializers.CharField(max_length=120, required=False, allow_blank=True, trim_whitespace=True,)
    platform = serializers.ChoiceField(choices=DevicePlatform.choices, default=DevicePlatform.ANDROID)
    push_token = serializers.CharField(min_length=20, max_length=512, trim_whitespace=True)


class DeviceQuietPeriodReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceQuietPeriod
        fields = [
            "id",
            "device",
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


class DeviceQuietPeriodWriteSerializer(serializers.ModelSerializer):
    period_type = serializers.ChoiceField(choices=QuietPeriodType.choices, required=False)
    recurrence_days = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        required=False,
    )

    class Meta:
        model = DeviceQuietPeriod
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
        period_type = validated_data.get("period_type", getattr(instance, "period_type", QuietPeriodType.ONCE))
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
        return DeviceQuietPeriod.objects.create(
            device=self.context["device"],
            **validated_data,
        )

    def update(self, instance, validated_data):
        validated_data = self._normalize_payload(validated_data, instance=instance)
        return super().update(instance, validated_data)
