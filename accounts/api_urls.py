from django.urls import path
from .api_views import (
    ConfirmEmailApiView,
    CustomTokenRefreshView,
    ForgotPasswordApiView,
    LoginApiView,
    LogoutApiView,
    MeApiView,
    RegisterApiView,
    ResendConfirmationApiView,
    ResetPasswordConfirmApiView,
)

urlpatterns = [
    path("register/", RegisterApiView.as_view(), name="auth-register"),
    path("email/confirm/", ConfirmEmailApiView.as_view(), name="auth-email-confirm"),
    path("email/resend/", ResendConfirmationApiView.as_view(), name="auth-email-resend"),
    path("login/", LoginApiView.as_view(), name="auth-login"),
    path("refresh/", CustomTokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", LogoutApiView.as_view(), name="auth-logout"),
    path("me/", MeApiView.as_view(), name="auth-me"),
    path("forgot-password/", ForgotPasswordApiView.as_view(), name="auth-forgot-password"),
    path("reset-password/", ResetPasswordConfirmApiView.as_view(), name="auth-reset-password"),
]