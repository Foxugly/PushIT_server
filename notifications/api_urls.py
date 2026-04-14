from django.urls import path
from .api_views import (
    NotificationBulkSendApiView,
    NotificationListCreateApiView,
    NotificationDetailApiView,
    NotificationFutureDetailApiView,
    NotificationFutureListApiView,
    NotificationSendApiView,
    NotificationStatsApiView,
)
from .api_views_app_token import (
    NotificationBulkSendWithAppTokenApiView,
    NotificationCreateWithAppTokenApiView,
    NotificationListWithAppTokenApiView,
)

urlpatterns = [
    path("", NotificationListCreateApiView.as_view(), name="notification-list-create"),
    path("future/", NotificationFutureListApiView.as_view(), name="notification-future-list"),
    path("future/<int:pk>/", NotificationFutureDetailApiView.as_view(), name="notification-future-detail"),
    path("<int:pk>/", NotificationDetailApiView.as_view(), name="notification-detail"),
    path("<int:notification_id>/send/", NotificationSendApiView.as_view(), name="notification-send"),
    path("bulk-send/", NotificationBulkSendApiView.as_view(), name="notification-bulk-send"),
    path("app/", NotificationListWithAppTokenApiView.as_view(), name="notification-list-app-token"),
    path("stats/", NotificationStatsApiView.as_view(), name="notification-stats"),
    path("app/create/", NotificationCreateWithAppTokenApiView.as_view(), name="notification-create-app-token"),
    path("app/bulk-send/", NotificationBulkSendWithAppTokenApiView.as_view(), name="notification-bulk-send-app-token"),
]
