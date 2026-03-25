from django.urls import path
from .api_views import (
    ApplicationListCreateApiView, ApplicationRegenerateTokenApiView,
    ApplicationRevokeTokenApiView, ApplicationActivateApiView, ApplicationDeactivateApiView,
)

urlpatterns = [
    path("", ApplicationListCreateApiView.as_view(), name="app-list-create"),
    path("<int:app_id>/regenerate-token/", ApplicationRegenerateTokenApiView.as_view(), name="app-regenerate-token"),
    path("<int:app_id>/activate/", ApplicationActivateApiView.as_view(), name="app-activate"),
    path("<int:app_id>/deactivate/", ApplicationDeactivateApiView.as_view(), name="app-deactivate"),
    path("<int:app_id>/revoke-token/", ApplicationRevokeTokenApiView.as_view(), name="app-revoke-token"),
]