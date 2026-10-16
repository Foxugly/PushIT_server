from django.urls import path

from .views import HealthLiveApiView, HealthReadyApiView, MetricsApiView


urlpatterns = [
    # Fleet-standard health URL (OPERATIONS.md §3.9) — same check as ready/.
    path("", HealthReadyApiView.as_view(), name="health"),
    path("live/", HealthLiveApiView.as_view(), name="health-live"),
    path("ready/", HealthReadyApiView.as_view(), name="health-ready"),
    path("metrics/", MetricsApiView.as_view(), name="health-metrics"),
]
