import logging
logger = logging.getLogger(__name__)

MESSAGE_PREVIEW_LENGTH = 160


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


def send_push_to_device(push_token: str, title: str, message: str) -> str:
    """
    Push provider wrapper.

    Currently uses a mock implementation. Distinguishes between permanent
    and temporary errors for when a real FCM provider is plugged in later.
    """

    log_extra = {
        "push_provider": "mock",
        "push_token_suffix": _get_push_token_suffix(push_token),
        "push_token_length": len(push_token),
        "notification_title": title,
        "notification_message_preview": _get_message_preview(message),
        "notification_message_length": len(message),
    }

    logger.info(
        "push_send_requested",
        extra=log_extra,
    )
    print(push_token)
    print(title)
    print(message)
    try:
        # TODO: plug in the real FCM provider here.
        # Future example:
        # provider_message_id = fcm_send(push_token=push_token, title=title, message=message)
        # return provider_message_id
        provider_message_id = f"mock-msg-{push_token[-6:]}"
        logger.info(
            "push_send_succeeded",
            extra={
                **log_extra,
                "provider_message_id": provider_message_id,
            },
        )
        return provider_message_id

    # Future example when FCM is plugged in:
    # except SomeFirebaseInvalidTokenException as exc:
    #     raise InvalidPushTokenError(str(exc)) from exc
    #
    # except SomeFirebaseTemporaryException as exc:
    #     raise TemporaryPushProviderError(str(exc)) from exc

    except InvalidPushTokenError as exc:
        logger.warning(
            "push_send_invalid_token",
            extra={
                **log_extra,
                "error": str(exc),
            },
        )
        raise
    except TemporaryPushProviderError as exc:
        logger.warning(
            "push_send_temporary_provider_error",
            extra={
                **log_extra,
                "error": str(exc),
            },
        )
        raise
    except PushProviderError as exc:
        logger.warning(
            "push_send_provider_error",
            extra={
                **log_extra,
                "error": str(exc),
            },
        )
        raise
    except Exception as exc:
        logger.exception(
            "push_send_unexpected_error",
            extra={
                **log_extra,
                "error": str(exc),
            },
        )
        raise PushProviderError(str(exc)) from exc
