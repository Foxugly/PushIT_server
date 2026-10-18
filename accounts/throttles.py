from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"


class RegisterRateThrottle(AnonRateThrottle):
    scope = "register"


class PasswordResetRateThrottle(AnonRateThrottle):
    scope = "password_reset"


class ResendEmailRateThrottle(AnonRateThrottle):
    scope = "resend"


class MagicLinkRateThrottle(AnonRateThrottle):
    scope = "magic_link"

class PasswordChangeRateThrottle(UserRateThrottle):
    """Per-user: a stolen access token must not be a current-password oracle."""

    scope = "password_change"
