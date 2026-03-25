import threading
import time
from collections import defaultdict

from django.db.models import Count

from devices.models import Device, DeviceTokenStatus
from notifications.models import Notification


PROCESS_START_TIME = time.time()


class MetricsRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters = defaultdict(float)

    @staticmethod
    def _normalize_labels(labels):
        if not labels:
            return ()
        return tuple(sorted((str(key), str(value)) for key, value in labels.items()))

    def increment(self, metric_name, amount=1, labels=None):
        key = (metric_name, self._normalize_labels(labels))
        with self._lock:
            self._counters[key] += amount

    def snapshot_counters(self):
        with self._lock:
            return dict(self._counters)


registry = MetricsRegistry()


def increment_counter(metric_name, *, amount=1, labels=None):
    registry.increment(metric_name, amount=amount, labels=labels)


def reset_metrics():
    with registry._lock:
        registry._counters.clear()


def _format_labels(labels):
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{value}"' for key, value in labels) + "}"


def _collect_database_gauges():
    lines = []

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


def render_metrics():
    lines = [
        "# HELP pushit_process_uptime_seconds Process uptime in seconds",
        "# TYPE pushit_process_uptime_seconds gauge",
        f"pushit_process_uptime_seconds {time.time() - PROCESS_START_TIME:.3f}",
    ]

    counters_by_name = defaultdict(list)
    for (metric_name, labels), value in registry.snapshot_counters().items():
        counters_by_name[metric_name].append((labels, value))

    for metric_name in sorted(counters_by_name):
        lines.append(f"# HELP {metric_name} Application metric")
        lines.append(f"# TYPE {metric_name} counter")
        for labels, value in sorted(counters_by_name[metric_name]):
            lines.append(f"{metric_name}{_format_labels(labels)} {value}")

    lines.extend(_collect_database_gauges())
    return "\n".join(lines) + "\n"
