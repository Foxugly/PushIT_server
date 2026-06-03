import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder

from .scheduling import (
    compute_effective_scheduled_map,
    filter_notifications_by_effective_range,
    filter_notifications_by_shift_flag,
    order_notifications_by_effective,
)


def compute_request_fingerprint(data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), cls=DjangoJSONEncoder)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def notification_filter_needs_effective(request, filter_data) -> bool:
    """True when the request needs the Python-computed effective-schedule map —
    i.e. an effective-schedule filter or an effective ordering is requested.

    When this is False (the common case), a list endpoint can paginate at the DB
    level instead of materialising the whole owner's queryset into memory.
    """
    has_quiet_period_shift = (
        "has_quiet_period_shift" in request.query_params
        and filter_data.get("has_quiet_period_shift") is not None
    )
    return bool(
        filter_data.get("effective_scheduled_from") is not None
        or filter_data.get("effective_scheduled_to") is not None
        or has_quiet_period_shift
        or filter_data.get("ordering")
    )


def apply_effective_schedule_filters(
    queryset,
    request,
    filter_data,
):
    effective_scheduled_from = filter_data.get("effective_scheduled_from")
    effective_scheduled_to = filter_data.get("effective_scheduled_to")
    has_quiet_period_shift = (
        filter_data.get("has_quiet_period_shift")
        if "has_quiet_period_shift" in request.query_params
        else None
    )
    ordering = filter_data.get("ordering")

    needs_effective_map = notification_filter_needs_effective(request, filter_data)

    effective_scheduled_map = (
        compute_effective_scheduled_map(queryset) if needs_effective_map else None
    )

    if effective_scheduled_from is not None or effective_scheduled_to is not None:
        queryset = filter_notifications_by_effective_range(
            queryset,
            effective_scheduled_map,
            effective_scheduled_from=effective_scheduled_from,
            effective_scheduled_to=effective_scheduled_to,
        )

    if has_quiet_period_shift is not None:
        queryset = filter_notifications_by_shift_flag(
            queryset,
            effective_scheduled_map,
            has_quiet_period_shift,
        )

    if ordering:
        queryset = order_notifications_by_effective(
            queryset, effective_scheduled_map, ordering
        )

    return queryset
