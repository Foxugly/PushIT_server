from django.urls import path
from .api_views import (
    DeviceListApiView,
    DeviceDetailApiView,
    DeviceQuietPeriodDetailApiView,
    DeviceQuietPeriodListCreateApiView,
)
from .api_views_app_token import DeviceIdentifyApiView, DeviceLinkWithAppTokenApiView

urlpatterns = [
    path("", DeviceListApiView.as_view(), name="device-list"),
    path("<int:pk>/", DeviceDetailApiView.as_view(), name="device-detail"),
    path("<int:device_id>/quiet-periods/", DeviceQuietPeriodListCreateApiView.as_view(), name="device-quiet-period-list-create"),
    path("<int:device_id>/quiet-periods/<int:quiet_period_id>/", DeviceQuietPeriodDetailApiView.as_view(), name="device-quiet-period-detail"),
    path("identify/", DeviceIdentifyApiView.as_view(), name="device-identify"),
    path("link/", DeviceLinkWithAppTokenApiView.as_view(), name="device-link"),

]
