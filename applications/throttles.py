from rest_framework.throttling import SimpleRateThrottle


class AppTokenRateThrottle(SimpleRateThrottle):
    scope = "app_token"

    def get_cache_key(self, request, view):
        application = getattr(request, "auth_application", None)
        if application is None:
            return None
        return self.cache_format % {"scope": self.scope, "ident": application.id}
