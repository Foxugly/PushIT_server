import re

from django.utils import timezone
from django.utils.dateparse import parse_datetime


SEND_AT_SUBJECT_PATTERN = re.compile(
    r"\[\s*SEND_AT\s*:\s*(?P<scheduled_for>[^\]]+)\s*\]",
    re.IGNORECASE,
)


def extract_subject_schedule(subject: str):
    match = SEND_AT_SUBJECT_PATTERN.search(subject)
    if match is None:
        return " ".join(subject.split()), None

    scheduled_for_raw = match.group("scheduled_for").strip()
    scheduled_for = parse_datetime(scheduled_for_raw)
    if scheduled_for is None or timezone.is_naive(scheduled_for):
        raise ValueError(
            "Invalid [SEND_AT:...] marker format. Use an ISO 8601 date with timezone."
        )

    title = SEND_AT_SUBJECT_PATTERN.sub("", subject, count=1)
    return " ".join(title.split()), scheduled_for
