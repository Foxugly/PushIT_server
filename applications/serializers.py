from drf_spectacular.utils import inline_serializer
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers

from config.api_errors import (
    ErrorResponseSerializer as DetailResponseSerializer,
    build_validation_error_serializer,
)
from .models import Application, ApplicationQuietPeriod, QuietPeriodType


# Logo upload limits. Kept conservative: a logo is a small UI asset, so a few MB
# and a couple thousand pixels per side is generous. Bounding both the byte size
# and the pixel dimensions guards against memory-exhaustion / decompression-bomb
# uploads (a tiny file can still decode to an enormous bitmap).
LOGO_MAX_BYTES = 2 * 1024 * 1024  # ~2 MB
LOGO_MAX_DIMENSION = 2048  # px per side
LOGO_ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}

# Defensive cap against decompression bombs: refuse to even decode images whose
# pixel count is absurd, before we look at the dimensions. Pillow raises rather
# than allocating gigabytes. Set to a hair above our own max so legitimate
# in-bounds images always decode.
Image.MAX_IMAGE_PIXELS = LOGO_MAX_DIMENSION * LOGO_MAX_DIMENSION * 2


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

ApplicationRegenerateEmailResponseSerializer = inline_serializer(
    name="ApplicationRegenerateEmailResponse",
    fields={
        "app_id": serializers.IntegerField(),
        "inbound_email_alias": serializers.CharField(),
        "inbound_email_address": serializers.CharField(),
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
    logo = serializers.ImageField(read_only=True, use_url=True)
    # Always serialized (empty string when unset, never null) — declare it
    # read-only so the schema marks it required + non-nullable, matching reality.
    webhook_url = serializers.CharField(read_only=True, allow_blank=True)

    class Meta:
        model = Application
        fields = [
            "id",
            "name",
            "description",
            "app_token_prefix",
            "inbound_email_alias",
            "inbound_email_address",
            "webhook_url",
            "logo",
            "is_active",
            "revoked_at",
            "last_used_at",
            "created_at",
        ]


class ApplicationLogoUploadSerializer(serializers.Serializer):
    """Multipart upload of an application logo image."""

    logo = serializers.ImageField()

    def validate_logo(self, value):
        if value.size > LOGO_MAX_BYTES:
            raise serializers.ValidationError(
                f"Logo file is too large (max {LOGO_MAX_BYTES // (1024 * 1024)} MB)."
            )

        # Inspect the decoded image: format restriction + dimension bound. Pillow
        # raises if the pixel count exceeds Image.MAX_IMAGE_PIXELS (bomb guard).
        try:
            value.seek(0)
            with Image.open(value) as img:
                image_format = img.format
                width, height = img.size
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
            raise serializers.ValidationError("Could not read the image file.")
        finally:
            value.seek(0)

        if image_format not in LOGO_ALLOWED_FORMATS:
            raise serializers.ValidationError(
                "Unsupported image format. Use PNG, JPEG, or WebP."
            )

        if width > LOGO_MAX_DIMENSION or height > LOGO_MAX_DIMENSION:
            raise serializers.ValidationError(
                f"Logo dimensions are too large "
                f"(max {LOGO_MAX_DIMENSION}x{LOGO_MAX_DIMENSION} px)."
            )

        return value


class ApplicationCreateSerializer(serializers.ModelSerializer):
    app_token = serializers.CharField(read_only=True)
    inbound_email_address = serializers.CharField(read_only=True)
    logo = serializers.ImageField(read_only=True, use_url=True)

    class Meta:
        model = Application
        fields = [
            "id",
            "name",
            "description",
            "webhook_url",
            "app_token_prefix",
            "inbound_email_alias",
            "inbound_email_address",
            "app_token",
            "logo",
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
            "logo",
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
        fields = ["name", "description", "webhook_url"]


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


class QuietPeriodWriteMixin:
    """Shared validation, normalization, and update logic for quiet period serializers."""

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
                errors["start_at"] = ["This field is required for a one-time period."]
            if end_at is None:
                errors["end_at"] = ["This field is required for a one-time period."]
            if start_at is not None and end_at is not None and end_at <= start_at:
                errors["end_at"] = ["Quiet period end must be after the start."]
            if recurrence_days:
                errors["recurrence_days"] = ["This field must be empty for a one-time period."]
            if start_time is not None:
                errors["start_time"] = ["This field must be null for a one-time period."]
            if end_time is not None:
                errors["end_time"] = ["This field must be null for a one-time period."]
        else:
            if not recurrence_days:
                errors["recurrence_days"] = ["At least one recurrence day is required."]
            if start_time is None:
                errors["start_time"] = ["This field is required for a recurring period."]
            if end_time is None:
                errors["end_time"] = ["This field is required for a recurring period."]
            if start_time is not None and end_time is not None and start_time == end_time:
                errors["end_time"] = ["End time must differ from start time."]
            if start_at is not None:
                errors["start_at"] = ["This field must be null for a recurring period."]
            if end_at is not None:
                errors["end_at"] = ["This field must be null for a recurring period."]

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

    def update(self, instance, validated_data):
        validated_data = self._normalize_payload(validated_data, instance=instance)
        return super().update(instance, validated_data)


class ApplicationQuietPeriodWriteSerializer(QuietPeriodWriteMixin, serializers.ModelSerializer):
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

    def create(self, validated_data):
        validated_data = self._normalize_payload(validated_data)
        return ApplicationQuietPeriod.objects.create(
            application=self.context["application"],
            **validated_data,
        )
