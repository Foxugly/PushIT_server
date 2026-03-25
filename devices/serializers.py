from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers
from config.api_errors import (
    ErrorResponseSerializer as DetailResponseSerializer,
    build_validation_error_serializer,
)
from .models import Device, DevicePlatform

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
