from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from config.api_errors import error_response
from .password_reset import confirm_password_reset, request_password_reset
from .turnstile import get_remote_ip, turnstile_enabled, verify_turnstile_token
from .api_serializers import (
    DetailResponseSerializer,
    ForgotPasswordSerializer,
    LanguageUpdateValidationErrorResponseSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    LoginValidationErrorResponseSerializer,
    LogoutSerializer,
    LogoutValidationErrorResponseSerializer,
    RegisterSerializer,
    RegisterValidationErrorResponseSerializer,
    ResetPasswordConfirmSerializer,
    TokenRefreshResponseSerializer,
    TokenRefreshValidationErrorResponseSerializer,
    UserLanguageUpdateSerializer,
    UserMeSerializer,
    build_token_response_for_user,
)
from .throttles import LoginRateThrottle, PasswordResetRateThrottle, RegisterRateThrottle


@extend_schema_view(
    post=extend_schema(
        summary="Create an account",
        description="Creates a new user account and returns the created profile.",
        tags=["Accounts"],
        auth=[],
        request=RegisterSerializer,
        responses={
            201: UserMeSerializer,
            400: OpenApiResponse(
                response=RegisterValidationErrorResponseSerializer,
                description="Invalid data",
            ),
            429: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Too many registration attempts",
            ),
        },
    )
)
class RegisterApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [RegisterRateThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Server-side Turnstile check before any DB write. Gated on the secret:
        # when no secret is configured the captcha is not yet provisioned, so we
        # skip it (register keeps working). Once configured, it is fail-closed —
        # any missing/invalid token or siteverify failure returns captcha_failed.
        if turnstile_enabled():
            token = serializer.validated_data.get("turnstile_token") or ""
            if not verify_turnstile_token(token, remote_ip=get_remote_ip(request)):
                return error_response(
                    code="captcha_failed",
                    detail="Captcha verification failed. Please try again.",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )

        user = serializer.save()
        return Response(UserMeSerializer(user).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    post=extend_schema(
        summary="Request a password reset",
        description=(
            "Sends a password-reset link to the email if it matches an active "
            "account. Always returns 200 with the same body (anti-leak), whether "
            "or not the email exists."
        ),
        tags=["Accounts"],
        auth=[],
        request=ForgotPasswordSerializer,
        responses={200: DetailResponseSerializer},
    )
)
class ForgotPasswordApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Turnstile gate (same model as register): verified only once a secret is
        # configured, fail-closed. Runs before the anti-leak no-op so a bot can't
        # use this endpoint to probe addresses without solving the captcha.
        if turnstile_enabled():
            token = serializer.validated_data.get("turnstile_token") or ""
            if not verify_turnstile_token(token, remote_ip=get_remote_ip(request)):
                return error_response(
                    code="captcha_failed",
                    detail="Captcha verification failed. Please try again.",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )

        request_password_reset(serializer.validated_data["email"])
        # Anti-leak: identical response whether or not the email matched.
        return Response(
            {"code": "ok", "detail": "If that email exists, a reset link has been sent."},
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    post=extend_schema(
        summary="Confirm a password reset",
        description="Sets a new password given the uid + token from the emailed link.",
        tags=["Accounts"],
        auth=[],
        request=ResetPasswordConfirmSerializer,
        responses={
            200: DetailResponseSerializer,
            400: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Invalid/expired link or weak password",
            ),
        },
    )
)
class ResetPasswordConfirmApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = ResetPasswordConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            ok = confirm_password_reset(data["uid"], data["token"], data["password"])
        except DjangoValidationError as exc:
            return error_response(
                code="password_invalid",
                detail=" ".join(exc.messages),
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        if not ok:
            return error_response(
                code="reset_link_invalid",
                detail="This reset link is invalid or has expired. Please request a new one.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"code": "ok", "detail": "Your password has been reset. You can now sign in."},
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    post=extend_schema(
        request=LoginSerializer,
        auth=[],
        responses={
            200: LoginResponseSerializer,
            400: OpenApiResponse(
                response=LoginValidationErrorResponseSerializer,
                description="Invalid credentials or invalid data",
            ),
        },
        summary="User login",
        description="Authenticates a user and returns JWT tokens.",
        tags=["Accounts"],
    )
)
class LoginApiView(APIView):
    serializer_class = LoginSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        return Response(build_token_response_for_user(user), status=status.HTTP_200_OK)


@extend_schema_view(
    post=extend_schema(
        request=LogoutSerializer,
        auth=[{"BearerAuth": []}],
        responses={
            204: None,
            400: OpenApiResponse(
                response=LogoutValidationErrorResponseSerializer,
                description="Invalid refresh token or invalid data",
            ),
        },
        summary="Logout",
        description="Blacklists the current refresh token.",
        tags=["Accounts"],
    )
)
class LogoutApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = LogoutSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        refresh_token = serializer.validated_data["refresh"]
        if not refresh_token:
            return error_response(
                code="refresh_token_required",
                detail="Refresh token is required.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except Exception:
            return error_response(
                code="refresh_token_invalid",
                detail="Invalid refresh token.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(
        responses=UserMeSerializer,
        summary="Current profile",
        description="Returns the currently authenticated user.",
        tags=["Accounts"],
        auth=[{"BearerAuth": []}],
    ),
    patch=extend_schema(
        request=UserLanguageUpdateSerializer,
        responses={
            200: UserMeSerializer,
            400: OpenApiResponse(
                response=LanguageUpdateValidationErrorResponseSerializer,
                description="Invalid data",
            ),
        },
        summary="Update profile language",
        description="Updates the preferred language of the authenticated user.",
        tags=["Accounts"],
        auth=[{"BearerAuth": []}],
    ),
)
class MeApiView(APIView):
    serializer_class = UserMeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(self.serializer_class(request.user).data)

    def patch(self, request):
        serializer = UserLanguageUpdateSerializer(request.user, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(self.serializer_class(request.user).data, status=status.HTTP_200_OK)


@extend_schema_view(
    post=extend_schema(
        summary="Refresh a token",
        description="Generates a new access token from a refresh token.",
        tags=["Accounts"],
        auth=[],
        responses={
            200: TokenRefreshResponseSerializer,
            400: OpenApiResponse(
                response=TokenRefreshValidationErrorResponseSerializer,
                description="Invalid refresh token or invalid data",
            ),
        },
    )
)
class CustomTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)
