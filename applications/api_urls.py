from django.urls import path
from notifications.api_views_templates import (
    NotificationTemplateListCreateApiView,
    NotificationTemplateDetailApiView,
)
from .api_views import (
    ApplicationDetailApiView, ApplicationListCreateApiView, ApplicationRegenerateTokenApiView,
    ApplicationRevokeTokenApiView, ApplicationActivateApiView, ApplicationDeactivateApiView,
    ApplicationQuietPeriodListCreateApiView, ApplicationQuietPeriodDetailApiView,
)

urlpatterns = [
    path("", ApplicationListCreateApiView.as_view(), name="app-list-create"),
    path("<int:app_id>/", ApplicationDetailApiView.as_view(), name="app-detail"),
    path("<int:app_id>/regenerate-token/", ApplicationRegenerateTokenApiView.as_view(), name="app-regenerate-token"),
    path("<int:app_id>/activate/", ApplicationActivateApiView.as_view(), name="app-activate"),
    path("<int:app_id>/deactivate/", ApplicationDeactivateApiView.as_view(), name="app-deactivate"),
    path("<int:app_id>/revoke-token/", ApplicationRevokeTokenApiView.as_view(), name="app-revoke-token"),
    path("<int:app_id>/quiet-periods/", ApplicationQuietPeriodListCreateApiView.as_view(), name="app-quiet-period-list-create"),
    path("<int:app_id>/quiet-periods/<int:quiet_period_id>/", ApplicationQuietPeriodDetailApiView.as_view(), name="app-quiet-period-detail"),
    path("<int:app_id>/templates/", NotificationTemplateListCreateApiView.as_view(), name="app-template-list-create"),
    path("<int:app_id>/templates/<int:template_id>/", NotificationTemplateDetailApiView.as_view(), name="app-template-detail"),
]
