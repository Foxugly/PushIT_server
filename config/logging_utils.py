import contextvars
import json
import logging
from datetime import datetime, timezone


request_id_var = contextvars.ContextVar("request_id", default="-")
incident_id_var = contextvars.ContextVar("incident_id", default="-")


def set_request_id(request_id: str):
    return request_id_var.set(request_id)


def reset_request_id(token) -> None:
    request_id_var.reset(token)


def get_request_id() -> str:
    return request_id_var.get()


def set_incident_id(incident_id: str):
    return incident_id_var.set(incident_id)


def reset_incident_id(token) -> None:
    incident_id_var.reset(token)


def get_incident_id() -> str:
    return incident_id_var.get()


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        if not hasattr(record, "incident_id"):
            record.incident_id = get_incident_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": getattr(record, "request_id", get_request_id()),
        }

        for key in (
            "incident_id",
            "error_code",
            "user_id",
            "application_id",
            "notification_id",
            "device_id",
            "task_id",
            "status",
            "target_count",
            "sent_count",
            "failed_count",
            "skipped_count",
            "attempt_count",
            "error",
            "path",
            "method",
            "push_provider",
            "push_token_suffix",
            "push_token_length",
            "notification_title",
            "notification_message_preview",
            "notification_message_length",
            "provider_message_id",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)
