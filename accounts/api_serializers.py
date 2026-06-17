from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from config.api_errors import (
    ErrorResponseSerializer as DetailResponseSerializer,
    build_validation_error_serializer,
)
from .models import User, UserLanguage


RegisterValidationErrorResponseSerializer = build_validation_error_serializer(
    "RegisterValidationErrorResponse",
    ["email", "password"],
)

LoginValidationErrorResponseSerializer = build_validation_error_serializer(
    "LoginValidationErrorResponse",
    ["email", "password"],
)

LogoutValidationErrorResponseSerializer = build_validation_error_serializer(
    "LogoutValidationErrorResponse",
    ["refresh"],
)

TokenRefreshValidationErrorResponseSerializer = build_validation_error_serializer(
    "TokenRefreshValidationErrorResponse",
    ["refresh"],
)

LanguageUpdateValidationErrorResponseSerializer = build_validation_error_serializer(
    "LanguageUpdateValidationErrorResponse",
    ["language"],
)

EmailConfirmValidationErrorResponseSerializer = build_validation_error_serializer(
    "EmailConfirmValidationErrorResponse",
    ["uid", "token"],
)

EmailResendValidationErrorResponseSerializer = build_validation_error_serializer(
    "EmailResendValidationErrorResponse",
    ["email"],
)


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    # Cloudflare Turnstile token. Optional at the serializer layer so the field
    # exists in the schema without breaking register while the captcha is not yet
    # provisioned; the view enforces it (fail-closed) only once a secret is set.
    turnstile_token = serializers.CharField(
        write_only=True, required=False, allow_blank=True
    )

    class Meta:
        model = User
        fields = ["email", "password", "turnstile_token"]

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("This email is already in use.")
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs["email"].strip().lower()
        password = attrs["password"]

        user = authenticate(
            request=self.context.get("request"),
            email=email,
            password=password,
        )

        if not user or not user.is_active:
            raise serializers.ValidationError("Invalid credentials.")

        attrs["user"] = user
        return attrs


class ForgotPasswordSerializer(serializers.Serializer):
    """Body of POST /auth/forgot-password/. Anti-leak: the view always returns
    200 regardless of whether the email matches a user."""

    email = serializers.EmailField()
    # Optional at the serializer layer; the view enforces Turnstile (fail-closed)
    # only once a secret is configured.
    turnstile_token = serializers.CharField(write_only=True, required=False, allow_blank=True)


class ResetPasswordConfirmSerializer(serializers.Serializer):
    """Body of POST /auth/reset-password/. `uid` + `token` come from the emailed
    link; `password` is the new password (validated against Django's validators
    in the service layer)."""

    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)


class EmailConfirmSerializer(serializers.Serializer):
    """Body of POST /auth/email/confirm/. `uid` + `token` come from the emailed
    link `{FRONTEND_BASE_URL}/auth/confirm-email/{uid}/{token}`."""

    uid = serializers.CharField()
    token = serializers.CharField()


class EmailResendSerializer(serializers.Serializer):
    """Body of POST /auth/email/resend/. Anti-leak: the view always returns 200."""

    email = serializers.EmailField()


class UserMeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        # is_staff / is_superuser are exposed read-only so the SPA can gate an
        # admin area client-side (the server still enforces IsAdminUser on the
        # admin endpoints). read_only_fields keeps them un-writable via /me/.
        fields = [
            "id", "email", "userkey", "is_active", "email_confirmed", "language",
            "is_staff", "is_superuser",
        ]
        read_only_fields = ["is_staff", "is_superuser"]


class UserLanguageUpdateSerializer(serializers.ModelSerializer):
    language = serializers.ChoiceField(choices=UserLanguage.choices)

    class Meta:
        model = User
        fields = ["language"]


class LoginResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserMeSerializer()


class TokenRefreshResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    # With ROTATE_REFRESH_TOKENS the refresh endpoint also returns a freshly
    # rotated refresh token; declare it so the OpenAPI schema matches runtime and
    # SPA/mobile clients persist the rotated token (see fleet JWT-rotation memo).
    refresh = serializers.CharField()


def build_token_response_for_user(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserMeSerializer(user).data,
    }


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(
        write_only=True,
        help_text="Refresh token to invalidate"
    )
