from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from applications.models import Application, ApplicationQuietPeriod, QuietPeriodType


MAX_QUIET_PERIOD_SHIFT_ITERATIONS = 32


def _local_time(value: datetime):
    return timezone.localtime(value).time().replace(tzinfo=None)


def _build_local_datetime(date_value, time_value):
    return timezone.make_aware(
        datetime.combine(date_value, time_value),
        timezone.get_current_timezone(),
    )


def get_quiet_period_end_for_period(quiet_period: ApplicationQuietPeriod, at: datetime):
    if not quiet_period.is_active:
        return None

    if quiet_period.period_type == QuietPeriodType.ONCE:
        if quiet_period.start_at is None or quiet_period.end_at is None:
            return None
        if quiet_period.start_at <= at < quiet_period.end_at:
            return quiet_period.end_at
        return None

    recurrence_days = quiet_period.recurrence_days or []
    if not recurrence_days or quiet_period.start_time is None or quiet_period.end_time is None:
        return None

    local_at = timezone.localtime(at)
    weekday = local_at.weekday()
    local_time = _local_time(at)

    if quiet_period.end_time > quiet_period.start_time:
        if weekday in recurrence_days and quiet_period.start_time <= local_time < quiet_period.end_time:
            return _build_local_datetime(local_at.date(), quiet_period.end_time)
        return None

    if weekday in recurrence_days and local_time >= quiet_period.start_time:
        return _build_local_datetime(local_at.date() + timedelta(days=1), quiet_period.end_time)

    previous_weekday = (weekday - 1) % 7
    if previous_weekday in recurrence_days and local_time < quiet_period.end_time:
        return _build_local_datetime(local_at.date(), quiet_period.end_time)

    return None


def get_quiet_period_end_for_application(application: Application, at: datetime):
    quiet_periods = ApplicationQuietPeriod.objects.filter(
        application=application,
        is_active=True,
    ).order_by("id")
    return get_quiet_period_end_from_iterable(quiet_periods, at)


def get_quiet_period_end_from_iterable(quiet_periods, at: datetime):
    matching_end_dates = [
        quiet_period_end
        for quiet_period in quiet_periods
        if (quiet_period_end := get_quiet_period_end_for_period(quiet_period, at)) is not None
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
    for _ in range(MAX_QUIET_PERIOD_SHIFT_ITERATIONS):
        if quiet_periods is None:
            quiet_period_end = get_quiet_period_end_for_application(application, effective)
        else:
            quiet_period_end = get_quiet_period_end_from_iterable(quiet_periods, effective)
        if quiet_period_end is None:
            return effective
        if quiet_period_end <= effective:
            return effective
        effective = quiet_period_end
    return effective


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
