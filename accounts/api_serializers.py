from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from config.api_errors import (
    ErrorResponseSerializer as DetailResponseSerializer,
    build_validation_error_serializer,
)
from .models import User, UserLanguage
from .refresh_cookie import PERSIST_CLAIM


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

MagicLinkRequestValidationErrorResponseSerializer = build_validation_error_serializer(
    "MagicLinkRequestValidationErrorResponse",
    ["email"],
)

MagicLinkVerifyValidationErrorResponseSerializer = build_validation_error_serializer(
    "MagicLinkVerifyValidationErrorResponse",
    ["token"],
)


class RegisterSerializer(serializers.ModelSerializer):
    # Declared explicitly with no validators so DRF's auto-added UniqueValidator
    # (from User.email's unique=True) does NOT fire: a duplicate email must not
    # 400 here (account-enumeration leak). The view detects the duplicate and
    # responds with the same pending body instead. Still an EmailField → format
    # validation is kept.
    email = serializers.EmailField(validators=[])
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
        # Normalize only. We deliberately do NOT reject an already-registered
        # email here: returning a "this email is in use" 400 would leak account
        # existence (enumeration). The view handles a duplicate by returning the
        # same pending-verification body and notifying the existing user instead.
        return value.strip().lower()

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
    remember_me = serializers.BooleanField(
        required=False,
        default=True,
        help_text=(
            "Cookie mode only (`X-Auth-Mode: cookie`): false makes the refresh "
            "cookie a browser-session cookie (no Max-Age). Ignored in body mode."
        ),
    )

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


class MagicLinkRequestSerializer(serializers.Serializer):
    """Body of POST /auth/magic-link/. Anti-leak: the view always returns 200
    regardless of whether the email matches a confirmed user."""

    email = serializers.EmailField()
    # Optional at the serializer layer; the view enforces Turnstile (fail-closed)
    # only once a secret is configured (same rollout gate as register/forgot).
    turnstile_token = serializers.CharField(write_only=True, required=False, allow_blank=True)


class MagicLinkVerifySerializer(serializers.Serializer):
    """Body of POST /auth/magic-link/verify/. `token` is the single-use token
    from the emailed link ({FRONTEND_BASE_URL}/auth/magic-link/{token})."""

    token = serializers.CharField()


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


REFRESH_COOKIE_NOTE = (
    " Omitted when the request sent `X-Auth-Mode: cookie`: the token is then "
    "set in the HttpOnly refresh cookie instead."
)


class LoginResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField(required=False, help_text="Refresh token." + REFRESH_COOKIE_NOTE)
    user = UserMeSerializer()


class TokenRefreshResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    # With ROTATE_REFRESH_TOKENS the refresh endpoint also returns a freshly
    # rotated refresh token; declare it so the OpenAPI schema matches runtime and
    # SPA/mobile clients persist the rotated token (see fleet JWT-rotation memo).
    refresh = serializers.CharField(
        required=False, help_text="Rotated refresh token." + REFRESH_COOKIE_NOTE
    )


def build_token_response_for_user(user: User, *, persist: bool = True) -> dict:
    refresh = RefreshToken.for_user(user)
    if not persist:
        # Survives rotation (simplejwt rotates the same token object), so the
        # refresh cookie stays a session cookie for the whole session.
        refresh[PERSIST_CLAIM] = False
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserMeSerializer(user).data,
    }


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(
        write_only=True,
        required=False,
        help_text=(
            "Refresh token to invalidate. Required in body mode; in cookie mode "
            "(`X-Auth-Mode: cookie`) the HttpOnly refresh cookie is used when omitted."
        ),
    )


class TokenRefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField(
        write_only=True,
        required=False,
        help_text=(
            "Refresh token. Required in body mode; in cookie mode "
            "(`X-Auth-Mode: cookie`) the HttpOnly refresh cookie is used when omitted."
        ),
    )


class AccountDeleteSerializer(serializers.Serializer):
    """Body of DELETE /api/v1/auth/me/. The view does the password check."""

    current_password = serializers.CharField(
        write_only=True,
        help_text="Current password, re-entered to confirm the irreversible deletion",
    )


AccountDeleteValidationErrorResponseSerializer = build_validation_error_serializer(
    "AccountDeleteValidationErrorResponse",
    ["current_password"],
)


class StaffUserSerializer(serializers.ModelSerializer):
    """Vue staff d'un compte : identité et état de l'accès offert.

    Seuls `subscription_bypass` et `bypass_note` sont mutables ;
    `bypass_granted_at` est horodaté par la vue, jamais transmis par le client.
    Rien d'autre n'est modifiable ici — l'email, le mot de passe et l'activation
    restent du ressort de l'admin Django.
    """

    class Meta:
        model = User
        fields = [
            "id", "email", "userkey", "is_active", "email_confirmed",
            "subscription_bypass", "bypass_note", "bypass_granted_at",
        ]
        read_only_fields = ["id", "email", "userkey", "is_active", "email_confirmed", "bypass_granted_at"]


class PasswordChangeSerializer(serializers.Serializer):
    """Body of POST /api/v1/auth/password/change/. The view does the checks so
    each failure gets its own stable `code`."""

    current_password = serializers.CharField(
        write_only=True, trim_whitespace=False, help_text="Current password"
    )
    new_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        help_text="New password, checked against Django's password validators",
    )


PasswordChangeValidationErrorResponseSerializer = build_validation_error_serializer(
    "PasswordChangeValidationErrorResponse",
    ["current_password", "new_password"],
)


class PasswordChangeResponseSerializer(serializers.Serializer):
    code = serializers.CharField(help_text="Always `password_changed`")
    detail = serializers.CharField()
    access = serializers.CharField(help_text="Fresh access token for the calling session")
    refresh = serializers.CharField(
        required=False,
        help_text=(
            "Fresh refresh token: every previously issued refresh token is now "
            "blacklisted, so the client MUST persist this one." + REFRESH_COOKIE_NOTE
        ),
    )
    user = UserMeSerializer()
