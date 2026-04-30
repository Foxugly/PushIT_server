from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers
from config.api_errors import (
    ErrorResponseSerializer as DetailResponseSerializer,
    build_validation_error_serializer,
)
from applications.models import QuietPeriodType
from applications.serializers import QuietPeriodWriteMixin
from .models import Device, DevicePlatform, DeviceQuietPeriod

class DeviceLinkedApplicationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    is_active = serializers.BooleanField()
    linked_at = serializers.DateTimeField()


class DeviceIdentifyResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    device_id = serializers.IntegerField()
    device_created = serializers.BooleanField()
    linked_applications = DeviceLinkedApplicationSerializer(many=True)

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
    ["app_token", "device_name", "platform", "push_token"],
)

DeviceIdentifyValidationErrorResponseSerializer = build_validation_error_serializer(
    "DeviceIdentifyValidationErrorResponse",
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
    app_token = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=True)
    device_name = serializers.CharField(max_length=120, required=False, allow_blank=True, trim_whitespace=True,)
    platform = serializers.ChoiceField(choices=DevicePlatform.choices, default=DevicePlatform.ANDROID)
    push_token = serializers.CharField(min_length=20, max_length=512, trim_whitespace=True)


class DeviceIdentifySerializer(serializers.Serializer):
    device_name = serializers.CharField(max_length=120, required=False, allow_blank=True, trim_whitespace=True)
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


class DeviceQuietPeriodWriteSerializer(QuietPeriodWriteMixin, serializers.ModelSerializer):
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

    def create(self, validated_data):
        validated_data = self._normalize_payload(validated_data)
        return DeviceQuietPeriod.objects.create(
            device=self.context["device"],
            **validated_data,
        )
