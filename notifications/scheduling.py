from __future__ import annotations

from datetime import datetime

from applications.models import Application, ApplicationQuietPeriod


def get_quiet_period_end_for_application(application: Application, at: datetime):
    return (
        ApplicationQuietPeriod.objects.filter(
            application=application,
            is_active=True,
            start_at__lte=at,
            end_at__gt=at,
        )
        .order_by("-end_at")
        .values_list("end_at", flat=True)
        .first()
    )


def get_quiet_period_end_from_iterable(quiet_periods, at: datetime):
    matching_end_dates = [
        quiet_period.end_at
        for quiet_period in quiet_periods
        if quiet_period.is_active and quiet_period.start_at <= at < quiet_period.end_at
    ]
    if not matching_end_dates:
        return None
    return max(matching_end_dates)


def compute_effective_scheduled_for(
    application: Application,
    scheduled_for: datetime | None,
    quiet_periods=None,
):
    if scheduled_for is None:
        return None

    effective = scheduled_for
    while True:
        if quiet_periods is None:
            quiet_period_end = get_quiet_period_end_for_application(application, effective)
        else:
            quiet_period_end = get_quiet_period_end_from_iterable(quiet_periods, effective)
        if quiet_period_end is None:
            return effective
        if quiet_period_end <= effective:
            return effective
        effective = quiet_period_end


def compute_effective_scheduled_map(notifications):
    effective_scheduled_map = {}
    for notification in notifications:
        quiet_periods = getattr(
            notification.application,
            "_prefetched_objects_cache",
            {},
        ).get("quiet_periods")
        effective_scheduled_map[notification.id] = compute_effective_scheduled_for(
            notification.application,
            notification.scheduled_for,
            quiet_periods=quiet_periods,
        )
    return effective_scheduled_map


def filter_notifications_by_effective_range(
    notifications,
    effective_scheduled_map,
    *,
    effective_scheduled_from=None,
    effective_scheduled_to=None,
):
    filtered_notifications = []
    for notification in notifications:
        effective_scheduled_for = effective_scheduled_map[notification.id]
        if effective_scheduled_from is not None:
            if effective_scheduled_for is None or effective_scheduled_for < effective_scheduled_from:
                continue
        if effective_scheduled_to is not None:
            if effective_scheduled_for is None or effective_scheduled_for > effective_scheduled_to:
                continue
        filtered_notifications.append(notification)
    return filtered_notifications


def filter_notifications_by_shift_flag(notifications, effective_scheduled_map, has_quiet_period_shift: bool):
    filtered_notifications = []
    for notification in notifications:
        effective_scheduled_for = effective_scheduled_map[notification.id]
        is_shifted = (
            notification.scheduled_for is not None
            and effective_scheduled_for is not None
            and effective_scheduled_for != notification.scheduled_for
        )
        if is_shifted == has_quiet_period_shift:
            filtered_notifications.append(notification)
    return filtered_notifications


def order_notifications_by_effective(notifications, effective_scheduled_map, ordering: str):
    descending = ordering.startswith("-")
    with_effective = [
        notification for notification in notifications if effective_scheduled_map[notification.id] is not None
    ]
    without_effective = [
        notification for notification in notifications if effective_scheduled_map[notification.id] is None
    ]

    with_effective = sorted(
        with_effective,
        key=lambda notification: (effective_scheduled_map[notification.id], notification.id),
        reverse=descending,
    )
    return with_effective + without_effective
