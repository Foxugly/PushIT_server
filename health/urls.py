from django.urls import path

from .views import HealthLiveApiView, HealthReadyApiView, MetricsApiView


urlpatterns = [
    path("live/", HealthLiveApiView.as_view(), name="health-live"),
    path("ready/", HealthReadyApiView.as_view(), name="health-ready"),
    path("metrics/", MetricsApiView.as_view(), name="health-metrics"),
]
