from applications.authentication import AppTokenPrincipal
from rest_framework.permissions import BasePermission


class HasAppToken(BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        auth = getattr(request, "auth", None)
        auth_application = getattr(request, "auth_application", None)

        if auth is None and auth_application is None:
            self.message = "App token manquant."
            self.code = "app_token_missing"
            return False

        if not isinstance(user, AppTokenPrincipal):
            self.message = "Contexte d'authentification applicative invalide."
            self.code = "app_token_context_invalid"
            return False

        if auth is None or auth_application is None or auth_application is not auth:
            self.message = "Contexte d'authentification applicative incoherent."
            self.code = "app_token_context_invalid"
            return False

        return True
