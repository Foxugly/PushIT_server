from django.urls import path
from .api_views import (
    NotificationListCreateApiView,
    NotificationDetailApiView,
    NotificationSendApiView, NotificationStatsApiView,
)
from .api_views_app_token import (
    NotificationCreateWithAppTokenApiView,
    NotificationListWithAppTokenApiView,
)

urlpatterns = [
    path("", NotificationListCreateApiView.as_view(), name="notification-list-create"),
    path("<int:pk>/", NotificationDetailApiView.as_view(), name="notification-detail"),
    path("<int:notification_id>/send/", NotificationSendApiView.as_view(), name="notification-send"),
    path("app/", NotificationListWithAppTokenApiView.as_view(), name="notification-list-app-token"),
    path("stats/", NotificationStatsApiView.as_view(), name="notification-stats"),
    path("app/create/", NotificationCreateWithAppTokenApiView.as_view(), name="notification-create-app-token"),

]
