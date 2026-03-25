from django.urls import path
from .api_views import DeviceListApiView, DeviceDetailApiView
from .api_views_app_token import DeviceLinkWithAppTokenApiView

urlpatterns = [
    path("", DeviceListApiView.as_view(), name="device-list"),
    path("<int:pk>/", DeviceDetailApiView.as_view(), name="device-detail"),
    path("link/", DeviceLinkWithAppTokenApiView.as_view(), name="device-link"),

]
