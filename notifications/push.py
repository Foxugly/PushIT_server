import logging

from django.conf import settings

logger = logging.getLogger(__name__)

MESSAGE_PREVIEW_LENGTH = 160

_fcm_initialized = False


def _get_push_token_suffix(push_token: str) -> str:
    return push_token[-8:] if len(push_token) > 8 else push_token


def _get_message_preview(message: str) -> str:
    if len(message) <= MESSAGE_PREVIEW_LENGTH:
        return message
    return f"{message[:MESSAGE_PREVIEW_LENGTH]}..."


class PushProviderError(Exception):
    pass


class InvalidPushTokenError(PushProviderError):
    pass


class TemporaryPushProviderError(PushProviderError):
    pass


def _is_fcm_configured() -> bool:
    return bool(getattr(settings, "FCM_SERVICE_ACCOUNT_PATH", ""))


def _ensure_fcm_initialized():
    global _fcm_initialized
    if _fcm_initialized:
        return
    import firebase_admin
    from firebase_admin import credentials

    cred = credentials.Certificate(settings.FCM_SERVICE_ACCOUNT_PATH)
    firebase_admin.initialize_app(cred)
    _fcm_initialized = True
    logger.info("fcm_initialized", extra={"service_account": settings.FCM_SERVICE_ACCOUNT_PATH})


def _send_fcm(push_token: str, title: str, message: str) -> str:
    _ensure_fcm_initialized()
    from firebase_admin import messaging
    from firebase_admin.exceptions import InvalidArgumentError, UnavailableError

    fcm_message = messaging.Message(
        notification=messaging.Notification(title=title, body=message),
        token=push_token,
    )

    try:
        return messaging.send(fcm_message)
    except (messaging.UnregisteredError, InvalidArgumentError) as exc:
        raise InvalidPushTokenError(str(exc)) from exc
    except UnavailableError as exc:
        raise TemporaryPushProviderError(str(exc)) from exc
    except messaging.FirebaseError as exc:
        raise PushProviderError(str(exc)) from exc


def _send_mock(push_token: str, title: str, message: str) -> str:
    return f"mock-msg-{push_token[-6:]}"


def send_push_to_device(push_token: str, title: str, message: str) -> str:
    provider = "fcm" if _is_fcm_configured() else "mock"
    log_extra = {
        "push_provider": provider,
        "push_token_suffix": _get_push_token_suffix(push_token),
        "push_token_length": len(push_token),
        "notification_title": title,
        "notification_message_preview": _get_message_preview(message),
        "notification_message_length": len(message),
    }

    logger.info("push_send_requested", extra=log_extra)

    try:
        if provider == "fcm":
            provider_message_id = _send_fcm(push_token, title, message)
        else:
            provider_message_id = _send_mock(push_token, title, message)

        logger.info(
            "push_send_succeeded",
            extra={**log_extra, "provider_message_id": provider_message_id},
        )
        return provider_message_id

    except InvalidPushTokenError as exc:
        logger.warning("push_send_invalid_token", extra={**log_extra, "error": str(exc)})
        raise
    except TemporaryPushProviderError as exc:
        logger.warning("push_send_temporary_provider_error", extra={**log_extra, "error": str(exc)})
        raise
    except PushProviderError as exc:
        logger.warning("push_send_provider_error", extra={**log_extra, "error": str(exc)})
        raise
    except Exception as exc:
        logger.exception("push_send_unexpected_error", extra={**log_extra, "error": str(exc)})
        raise PushProviderError(str(exc)) from exc
