from django.urls import path
from .api_views import RegisterApiView, LoginApiView, LogoutApiView, MeApiView, CustomTokenRefreshView

urlpatterns = [
    path("register/", RegisterApiView.as_view(), name="auth-register"),
    path("login/", LoginApiView.as_view(), name="auth-login"),
    path("refresh/", CustomTokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", LogoutApiView.as_view(), name="auth-logout"),
    path("me/", MeApiView.as_view(), name="auth-me"),
]