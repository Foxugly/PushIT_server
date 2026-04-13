from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from config.api_errors import error_response
from .api_serializers import (
    DetailResponseSerializer,
    LanguageUpdateValidationErrorResponseSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    LoginValidationErrorResponseSerializer,
    LogoutSerializer,
    LogoutValidationErrorResponseSerializer,
    RegisterSerializer,
    RegisterValidationErrorResponseSerializer,
    TokenRefreshResponseSerializer,
    TokenRefreshValidationErrorResponseSerializer,
    UserLanguageUpdateSerializer,
    UserMeSerializer,
    build_token_response_for_user,
)
from .throttles import LoginRateThrottle, RegisterRateThrottle


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
        user = serializer.save()
        return Response(UserMeSerializer(user).data, status=status.HTTP_201_CREATED)


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
