import logging
logger = logging.getLogger(__name__)



class PushProviderError(Exception):
    pass


class InvalidPushTokenError(PushProviderError):
    pass


class TemporaryPushProviderError(PushProviderError):
    pass


def send_push_to_device(push_token: str, title: str, message: str) -> str:
    """
    Wrapper provider.

    Pour l'instant :
    - garde un comportement mock pour ne pas casser le projet
    - permet déjà de distinguer les erreurs permanentes / temporaires
      si un vrai provider FCM est branché plus tard
    """

    try:
        # TODO: brancher le vrai provider FCM ici.
        # Exemple futur :
        # provider_message_id = fcm_send(push_token=push_token, title=title, message=message)
        # return provider_message_id
        return f"mock-msg-{push_token[-6:]}"

    # Exemple futur quand FCM sera branché :
    # except SomeFirebaseInvalidTokenException as exc:
    #     raise InvalidPushTokenError(str(exc)) from exc
    #
    # except SomeFirebaseTemporaryException as exc:
    #     raise TemporaryPushProviderError(str(exc)) from exc

    except InvalidPushTokenError:
        raise
    except TemporaryPushProviderError:
        raise
    except PushProviderError:
        raise
    except Exception as exc:
        raise PushProviderError(str(exc)) from exc