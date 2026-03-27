import os
import time

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, REGISTRY, generate_latest, multiprocess
from django.db.models import Count

from devices.models import Device, DeviceTokenStatus
from notifications.models import Notification


PROCESS_START_TIME = time.time()

PROMETHEUS_CONTENT_TYPE = CONTENT_TYPE_LATEST

_COUNTERS = {
    "pushit_http_requests_total": Counter(
        "pushit_http_requests_total",
        "HTTP requests grouped by method, route and status",
        ["method", "route", "status"],
    ),
    "pushit_app_token_auth_total": Counter(
        "pushit_app_token_auth_total",
        "Application token authentications grouped by outcome",
        ["outcome"],
    ),
    "pushit_notification_processing_total": Counter(
        "pushit_notification_processing_total",
        "Notification processing attempts grouped by outcome",
        ["outcome"],
    ),
    "pushit_notification_send_total": Counter(
        "pushit_notification_send_total",
        "Notification send attempts grouped by outcome",
        ["outcome"],
    ),
    "pushit_notification_delivery_total": Counter(
        "pushit_notification_delivery_total",
        "Notification deliveries grouped by outcome",
        ["outcome"],
    ),
}


def increment_counter(metric_name, *, amount=1, labels=None):
    counter = _COUNTERS[metric_name]
    normalized_labels = {str(key): str(value) for key, value in (labels or {}).items()}
    counter.labels(**normalized_labels).inc(amount)


def reset_metrics():
    for counter in _COUNTERS.values():
        with counter._lock:
            counter._metrics.clear()

def _collect_database_gauges():
    lines = []

    lines.extend(
        [
            "# HELP pushit_process_uptime_seconds Process uptime in seconds",
            "# TYPE pushit_process_uptime_seconds gauge",
            f"pushit_process_uptime_seconds {time.time() - PROCESS_START_TIME:.3f}",
        ]
    )

    notification_lines = [
        "# HELP pushit_notifications_total Total notifications grouped by status",
        "# TYPE pushit_notifications_total gauge",
    ]
    for status_name, count in (
        Notification.objects.values_list("status").order_by("status").annotate(count=Count("id"))
    ):
        notification_lines.append(
            f'pushit_notifications_total{{status="{status_name}"}} {count}'
        )

    device_lines = [
        "# HELP pushit_devices_total Total devices grouped by push token status",
        "# TYPE pushit_devices_total gauge",
    ]
    for status_name, count in (
        Device.objects.values_list("push_token_status").order_by("push_token_status").annotate(count=Count("id"))
    ):
        device_lines.append(
            f'pushit_devices_total{{push_token_status="{status_name}"}} {count}'
        )

    active_device_count = Device.objects.filter(
        push_token_status=DeviceTokenStatus.ACTIVE,
        is_active=True,
    ).count()
    device_lines.append(f"pushit_active_devices_total {active_device_count}")

    lines.extend(notification_lines)
    lines.extend(device_lines)
    return lines


def _build_metrics_registry():
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip()
    if not multiproc_dir:
        return REGISTRY

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return registry


def render_metrics():
    registry_payload = generate_latest(_build_metrics_registry()).decode("utf-8").strip()
    lines = registry_payload.splitlines() if registry_payload else []
    lines.extend(_collect_database_gauges())
    return "\n".join(lines) + "\n"
