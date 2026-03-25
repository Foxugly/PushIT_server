from drf_spectacular.extensions import OpenApiAuthenticationExtension


class AppTokenAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "applications.authentication.AppTokenAuthentication"
    name = "AppTokenAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "X-App-Token",
            "description": "Token serveur de l'application",
        }