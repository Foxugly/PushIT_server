from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from billing import client as billing_client
from billing.service import billing_configured
from config.api_errors import ErrorResponseSerializer, error_response
from .email_confirmation import (
    confirm_email,
    resend_confirmation_email,
    send_confirmation_email,
    send_duplicate_registration_email,
)
from .deletion import delete_user_account
from .password_change import change_password_and_rotate_sessions
from .magic_link import request_magic_link, verify_magic_link
from .models import User
from . import refresh_cookie
from .refresh_cookie import PERSIST_CLAIM, deliver_tokens
from .password_reset import confirm_password_reset, request_password_reset
from .turnstile import get_remote_ip, turnstile_enabled, verify_turnstile_token
from .api_serializers import (
    AccountDeleteSerializer,
    AccountDeleteValidationErrorResponseSerializer,
    DetailResponseSerializer,
    EmailConfirmSerializer,
    EmailConfirmValidationErrorResponseSerializer,
    EmailResendSerializer,
    EmailResendValidationErrorResponseSerializer,
    ForgotPasswordSerializer,
    LanguageUpdateValidationErrorResponseSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    LoginValidationErrorResponseSerializer,
    LogoutSerializer,
    LogoutValidationErrorResponseSerializer,
    MagicLinkRequestSerializer,
    MagicLinkRequestValidationErrorResponseSerializer,
    MagicLinkVerifySerializer,
    MagicLinkVerifyValidationErrorResponseSerializer,
    PasswordChangeResponseSerializer,
    PasswordChangeSerializer,
    PasswordChangeValidationErrorResponseSerializer,
    RegisterSerializer,
    RegisterValidationErrorResponseSerializer,
    ResetPasswordConfirmSerializer,
    TokenRefreshRequestSerializer,
    TokenRefreshResponseSerializer,
    TokenRefreshValidationErrorResponseSerializer,
    UserLanguageUpdateSerializer,
    UserMeSerializer,
    build_token_response_for_user,
)
from .throttles import (
    LoginRateThrottle,
    MagicLinkRateThrottle,
    PasswordChangeRateThrottle,
    PasswordResetRateThrottle,
    RegisterRateThrottle,
    ResendEmailRateThrottle,
)


AUTH_MODE_PARAMETER = OpenApiParameter(
    name="X-Auth-Mode",
    type=str,
    location=OpenApiParameter.HEADER,
    required=False,
    enum=["cookie"],
    description=(
        "Web SPA only. `cookie` moves the refresh token into an HttpOnly, Secure, "
        "SameSite=Lax cookie scoped to `/api/v1/auth/` (and omits `refresh` from the "
        "body); the request's `Origin` must then be in CORS_ALLOWED_ORIGINS (403 "
        "`origin_not_allowed`). Absent: body tokens (mobile app)."
    ),
)


def _cookie_origin_guard(request):
    """403 for a cookie-mode request from an origin that is not allowed (CSRF)."""
    if refresh_cookie.wants_cookie(request) and not refresh_cookie.origin_allowed(request):
        return refresh_cookie.origin_rejected_response()
    return None


class IsAuthenticatedOrCookieMode(permissions.BasePermission):
    """Logout: a Bearer token, or the cookie-mode opt-in (origin checked in the view).

    Lets the SPA sign out even when its in-memory access token is gone (expired,
    tab reloaded): blacklisting the caller's own refresh cookie needs nothing else.
    """

    def has_permission(self, request, view):
        if refresh_cookie.wants_cookie(request):
            return True
        return bool(request.user and request.user.is_authenticated)

@extend_schema_view(
    post=extend_schema(
        summary="Create an account",
        description=(
            "Creates a new (unconfirmed) account and emails a confirmation link. "
            "Returns 201 with code `registration_pending_verification` — NO tokens; "
            "the user must confirm their email before they can log in."
        ),
        tags=["Accounts"],
        auth=[],
        request=RegisterSerializer,
        responses={
            201: DetailResponseSerializer,
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

        email = serializer.validated_data["email"]

        # Anti-enumeration: never reveal whether the address is already taken.
        # For a duplicate we skip account creation, notify the genuine owner with
        # a neutral heads-up, and fall through to the SAME pending-verification
        # body a fresh signup gets — the caller cannot distinguish the two cases.
        existing = User.objects.filter(email=email).first()
        if existing is not None:
            send_duplicate_registration_email(existing)
        else:
            user = serializer.save()  # email_confirmed defaults to False
            send_confirmation_email(user)

        return Response(
            {
                "code": "registration_pending_verification",
                "detail": "Account created. Check your inbox to confirm your email before signing in.",
                "email": email,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    post=extend_schema(
        summary="Confirm an email address",
        description=(
            "Confirms the account from the uid + token in the emailed link, then "
            "returns JWT tokens (auto-login). Idempotent for an already-confirmed user."
        ),
        tags=["Accounts"],
        auth=[],
        request=EmailConfirmSerializer,
        parameters=[AUTH_MODE_PARAMETER],
        responses={
            200: LoginResponseSerializer,
            400: OpenApiResponse(
                response=EmailConfirmValidationErrorResponseSerializer,
                description="Invalid/expired confirmation link",
            ),
        },
    )
)
class ConfirmEmailApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        if (rejected := _cookie_origin_guard(request)) is not None:
            return rejected
        serializer = EmailConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = confirm_email(data["uid"], data["token"])
        if user is None:
            return error_response(
                code="confirmation_link_invalid",
                detail="This confirmation link is invalid or has expired. Request a new one.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        return deliver_tokens(
            request, Response(build_token_response_for_user(user), status=status.HTTP_200_OK)
        )


@extend_schema_view(
    post=extend_schema(
        summary="Resend the confirmation email",
        description=(
            "Re-sends the confirmation link if the email matches an unconfirmed "
            "active account. Always returns 200 with the same body (anti-leak)."
        ),
        tags=["Accounts"],
        auth=[],
        request=EmailResendSerializer,
        responses={
            200: DetailResponseSerializer,
            400: OpenApiResponse(
                response=EmailResendValidationErrorResponseSerializer,
                description="Invalid data",
            ),
        },
    )
)
class ResendConfirmationApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ResendEmailRateThrottle]

    def post(self, request):
        serializer = EmailResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resend_confirmation_email(serializer.validated_data["email"])
        # Anti-leak: identical response whether or not the email matched.
        return Response(
            {"code": "ok", "detail": "If that email needs confirmation, a new link has been sent."},
            status=status.HTTP_200_OK,
        )


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
        summary="Request a passwordless magic-link",
        description=(
            "Emails a single-use, time-limited sign-in link to the address if it "
            "matches an active, email-confirmed account. Always returns 200 with "
            "the same body (anti-leak), whether or not the email exists."
        ),
        tags=["Accounts"],
        auth=[],
        request=MagicLinkRequestSerializer,
        responses={
            200: DetailResponseSerializer,
            400: OpenApiResponse(
                response=MagicLinkRequestValidationErrorResponseSerializer,
                description="Invalid data or captcha failure",
            ),
        },
    )
)
class MagicLinkRequestApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [MagicLinkRateThrottle]

    def post(self, request):
        serializer = MagicLinkRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Turnstile gate (same model as register/forgot-password): verified only
        # once a secret is configured, fail-closed. Runs before the anti-leak
        # no-op so a bot can't probe addresses without solving the captcha.
        if turnstile_enabled():
            token = serializer.validated_data.get("turnstile_token") or ""
            if not verify_turnstile_token(token, remote_ip=get_remote_ip(request)):
                return error_response(
                    code="captcha_failed",
                    detail="Captcha verification failed. Please try again.",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )

        request_magic_link(serializer.validated_data["email"])
        # Anti-leak: identical response whether or not the email matched.
        return Response(
            {"code": "ok", "detail": "If that email is registered, a sign-in link has been sent."},
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    post=extend_schema(
        summary="Verify a magic-link and sign in",
        description=(
            "Validates the single-use token from the emailed link, marks it used, "
            "and returns JWT tokens (auto-login). Rejects an invalid, expired, or "
            "already-used token with 400."
        ),
        tags=["Accounts"],
        auth=[],
        request=MagicLinkVerifySerializer,
        parameters=[AUTH_MODE_PARAMETER],
        responses={
            200: LoginResponseSerializer,
            400: OpenApiResponse(
                response=MagicLinkVerifyValidationErrorResponseSerializer,
                description="Invalid/expired/used link or invalid data",
            ),
        },
    )
)
class MagicLinkVerifyApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        if (rejected := _cookie_origin_guard(request)) is not None:
            return rejected
        serializer = MagicLinkVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = verify_magic_link(serializer.validated_data["token"])
        if user is None:
            return error_response(
                code="magic_link_invalid",
                detail="This sign-in link is invalid, expired, or already used.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        return deliver_tokens(
            request, Response(build_token_response_for_user(user), status=status.HTTP_200_OK)
        )


@extend_schema_view(
    post=extend_schema(
        request=LoginSerializer,
        auth=[],
        parameters=[AUTH_MODE_PARAMETER],
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
        if (rejected := _cookie_origin_guard(request)) is not None:
            return rejected
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        # Gate: credentials are valid, but the email must be confirmed first. The
        # `email_not_verified` code lets the SPA offer a "resend confirmation" CTA.
        if not user.email_confirmed:
            return error_response(
                code="email_not_verified",
                detail="Please confirm your email address before signing in.",
                http_status=status.HTTP_403_FORBIDDEN,
            )
        persist = serializer.validated_data["remember_me"] or not refresh_cookie.wants_cookie(request)
        return deliver_tokens(
            request,
            Response(build_token_response_for_user(user, persist=persist), status=status.HTTP_200_OK),
        )


@extend_schema_view(
    post=extend_schema(
        request=LogoutSerializer,
        auth=[{"BearerAuth": []}, {}],
        parameters=[AUTH_MODE_PARAMETER],
        responses={
            204: None,
            400: OpenApiResponse(
                response=LogoutValidationErrorResponseSerializer,
                description="Invalid refresh token or invalid data (body mode)",
            ),
            403: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Cookie mode from an origin that is not allowed (code=origin_not_allowed)",
            ),
        },
        summary="Logout",
        description=(
            "Blacklists the current refresh token. Body mode (Bearer required): "
            "`{refresh}`. Cookie mode (`X-Auth-Mode: cookie`, no Bearer needed): "
            "blacklists the body token or else the HttpOnly cookie, always clears "
            "the cookie and answers 204 (idempotent)."
        ),
        tags=["Accounts"],
    )
)
class LogoutApiView(APIView):
    permission_classes = [IsAuthenticatedOrCookieMode]
    serializer_class = LogoutSerializer

    def post(self, request):
        if (rejected := _cookie_origin_guard(request)) is not None:
            return rejected
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        refresh_token = serializer.validated_data.get("refresh", "")

        if refresh_cookie.wants_cookie(request):
            # Idempotent: whatever the token's state, the browser ends up signed out.
            refresh_token = refresh_token or refresh_cookie.read_refresh_cookie(request)
            if refresh_token:
                try:
                    RefreshToken(refresh_token).blacklist()
                except TokenError:
                    pass
            response = Response(status=status.HTTP_204_NO_CONTENT)
            refresh_cookie.clear_refresh_cookie(response)
            return response

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
    delete=extend_schema(
        request=AccountDeleteSerializer,
        responses={
            204: OpenApiResponse(description="Account deleted. No content."),
            400: OpenApiResponse(
                response=AccountDeleteValidationErrorResponseSerializer,
                description=(
                    "`current_password` missing (code=validation_error) or wrong "
                    "(code=current_password_invalid). Nothing is deleted."
                ),
            ),
            503: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    "The central billing service could not erase the user's billing "
                    "data (code=billing_unavailable). Nothing is deleted; retry later."
                ),
            ),
        },
        summary="Delete my account",
        description=(
            "Irreversibly deletes the authenticated user (GDPR erasure). Requires the "
            "current password. Cascades every application the user owns (with its "
            "notifications, deliveries, templates, send tokens and device links) even "
            "when other users' devices are subscribed to it, deletes the user's own "
            "devices, and blacklists every refresh token issued to the user. When "
            "billing is configured, the user is first erased at the central billing "
            "service (subscriptions cancelled immediately, Stripe customer deleted); "
            "if that fails, nothing is deleted (503 billing_unavailable)."
        ),
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

    def delete(self, request):
        serializer = AccountDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["current_password"]):
            return error_response(
                code="current_password_invalid",
                detail="Current password is incorrect.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        # Billing first: if the central cannot erase (cancel + delete the Stripe
        # customer), keep the local account so the user can retry.
        if billing_configured():
            try:
                billing_client.erase_user(str(request.user.pk))
            except billing_client.BillingUnavailable:
                return error_response(
                    code="billing_unavailable",
                    detail=(
                        "The billing service is temporarily unavailable; your account "
                        "was not deleted. Please try again later."
                    ),
                    http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        delete_user_account(request.user)
        response = Response(status=status.HTTP_204_NO_CONTENT)
        if refresh_cookie.wants_cookie(request):
            refresh_cookie.clear_refresh_cookie(response)
        return response


@extend_schema_view(
    post=extend_schema(
        summary="Change my password",
        description=(
            "Changes the authenticated user's password. Body `{current_password, "
            "new_password}`; `new_password` goes through Django's password "
            "validators (with the user as context) and must differ from the "
            "current one. On success **every refresh token previously issued to "
            "the user is blacklisted** (all other sessions are signed out at their "
            "next refresh) and a fresh `access` + `refresh` pair is returned so the "
            "calling session stays signed in: the client MUST replace its stored "
            "tokens with these. Throttled per user (`password_change`, 5/min)."
        ),
        tags=["Accounts"],
        auth=[{"BearerAuth": []}],
        request=PasswordChangeSerializer,
        parameters=[AUTH_MODE_PARAMETER],
        responses={
            200: PasswordChangeResponseSerializer,
            400: OpenApiResponse(
                response=PasswordChangeValidationErrorResponseSerializer,
                description=(
                    "A field is missing (code=validation_error), `current_password` "
                    "is wrong (code=current_password_invalid), `new_password` equals "
                    "the current one (code=password_unchanged), or the validators "
                    "reject it (code=password_invalid, messages in "
                    "`errors.new_password`). The password is unchanged."
                ),
            ),
            429: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Too many attempts (code=throttled)",
            ),
        },
    )
)
class PasswordChangeApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [PasswordChangeRateThrottle]

    def post(self, request):
        if (rejected := _cookie_origin_guard(request)) is not None:
            return rejected
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        current = serializer.validated_data["current_password"]
        new = serializer.validated_data["new_password"]
        user = request.user

        if not user.check_password(current):
            return error_response(
                code="current_password_invalid",
                detail="Current password is incorrect.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        if new == current:
            return error_response(
                code="password_unchanged",
                detail="The new password must be different from the current one.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_password(new, user=user)
        except DjangoValidationError as exc:
            return Response(
                {
                    "code": "password_invalid",
                    "detail": " ".join(exc.messages),
                    "errors": {"new_password": list(exc.messages)},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        persist = _cookie_session_persists(request)
        refresh = change_password_and_rotate_sessions(user, new)
        if not persist:
            refresh[PERSIST_CLAIM] = False
        response = Response(
            {
                "code": "password_changed",
                "detail": "Your password has been changed.",
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserMeSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )
        return deliver_tokens(request, response)


def _cookie_session_persists(request) -> bool:
    """Keep the "remember me" choice of the current cookie session across a re-mint."""
    if not refresh_cookie.wants_cookie(request):
        return True
    current = refresh_cookie.read_refresh_cookie(request)
    if not current:
        return True
    try:
        return RefreshToken(current).payload.get(PERSIST_CLAIM, True) is not False
    except TokenError:
        return True


@extend_schema_view(
    post=extend_schema(
        summary="Refresh a token",
        description=(
            "Generates a new access token from a refresh token and rotates the refresh "
            "token (the presented one is blacklisted). Body mode: `{refresh}` in, "
            "`{access, refresh}` out. Cookie mode (`X-Auth-Mode: cookie`): the body "
            "`refresh` is optional; when absent the HttpOnly cookie is used (401 "
            "`refresh_cookie_missing` if there is none); the rotated token is set in "
            "the cookie and omitted from the body. A body token sent in cookie mode "
            "is the one-time migration path for SPAs that still hold one in storage."
        ),
        tags=["Accounts"],
        auth=[],
        request=TokenRefreshRequestSerializer,
        parameters=[AUTH_MODE_PARAMETER],
        responses={
            200: TokenRefreshResponseSerializer,
            400: OpenApiResponse(
                response=TokenRefreshValidationErrorResponseSerializer,
                description="Invalid data (body mode without `refresh`)",
            ),
            401: OpenApiResponse(
                response=DetailResponseSerializer,
                description=(
                    "Refresh token invalid, expired or blacklisted (code=token_not_valid), "
                    "or no refresh cookie in cookie mode (code=refresh_cookie_missing)"
                ),
            ),
            403: OpenApiResponse(
                response=DetailResponseSerializer,
                description="Cookie mode from an origin that is not allowed (code=origin_not_allowed)",
            ),
        },
    )
)
class CustomTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        if not refresh_cookie.wants_cookie(request):
            return super().post(request, *args, **kwargs)

        if (rejected := _cookie_origin_guard(request)) is not None:
            return rejected
        body_token = request.data.get("refresh") if hasattr(request.data, "get") else None
        token = body_token or refresh_cookie.read_refresh_cookie(request)
        if not token:
            return error_response(
                code="refresh_cookie_missing",
                detail="No refresh cookie: sign in again.",
                http_status=status.HTTP_401_UNAUTHORIZED,
            )
        serializer = TokenRefreshSerializer(data={"refresh": token})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0]) from exc
        return deliver_tokens(request, Response(serializer.validated_data, status=status.HTTP_200_OK))
