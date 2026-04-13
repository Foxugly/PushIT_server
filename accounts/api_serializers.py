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
    ["email", "username", "password"],
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


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["email", "username", "password"]

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
            username=validated_data["username"],
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


class UserMeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "username", "userkey", "is_active", "language"]


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
