"""Gestion des jetons d'émission d'une application.

Trois gestes : lister, créer, révoquer — plus la révélation, qui est le seul
endroit où un jeton déjà créé peut être relu.

La révélation exige de **re-saisir le mot de passe**. C'est ce qui distingue
« relisible par toi » de « relisible par quiconque a atteint ta session » : un
onglet resté ouvert, un portable emprunté, une XSS ne suffisent pas.
"""
import logging

from django.contrib.auth import authenticate
from rest_framework import generics, permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import error_response

from .crypto import RevealUnavailable
from .models import Application
from .models_send_token import MAX_TOKENS_PER_APPLICATION, AppSendToken, SendTokenReveal

logger = logging.getLogger("pushit")


class SendTokenSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = AppSendToken
        fields = ["id", "name", "prefix", "created_at", "last_used_at", "revoked_at", "is_active"]
        read_only_fields = ["id", "prefix", "created_at", "last_used_at", "revoked_at"]


class RevealSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)


class _OwnedApplicationMixin:
    permission_classes = [permissions.IsAuthenticated]

    def get_application(self) -> Application:
        return generics.get_object_or_404(
            Application, pk=self.kwargs["app_id"], owner=self.request.user
        )


class SendTokenListCreateApiView(_OwnedApplicationMixin, generics.ListCreateAPIView):
    """GET : la liste. POST {name} : crée et renvoie la valeur **une seule fois**.

    Créer un jeton n'en révoque aucun autre : c'est ce qui permet de remplacer
    un jeton compromis sans interruption — on crée, on déploie, puis on révoque
    l'ancien. L'ancienne régénération coupait tout d'un coup.
    """

    serializer_class = SendTokenSerializer
    pagination_class = None

    def get_queryset(self):
        return self.get_application().send_tokens.all()

    def create(self, request, *args, **kwargs):
        application = self.get_application()
        if application.send_tokens.filter(revoked_at__isnull=True).count() >= MAX_TOKENS_PER_APPLICATION:
            return error_response(
                code="too_many_send_tokens",
                detail=f"Maximum {MAX_TOKENS_PER_APPLICATION} jetons actifs par application.",
                http_status=status.HTTP_409_CONFLICT,
            )

        nom = (request.data.get("name") or "").strip()
        if not nom:
            return error_response(
                code="missing_name",
                detail="Donnez un nom au jeton : c'est ce qui permet de savoir lequel révoquer.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        jeton, brut = AppSendToken.issue(application, nom[:60])
        logger.info("send_token_created", extra={"app": application.id, "token": jeton.id})
        return Response(
            {**SendTokenSerializer(jeton).data, "token": brut},
            status=status.HTTP_201_CREATED,
        )


class SendTokenRevokeApiView(_OwnedApplicationMixin, APIView):
    """DELETE : révoque. Immédiat, et sans effet sur les autres jetons."""

    def delete(self, request, app_id, token_id):
        jeton = generics.get_object_or_404(
            AppSendToken, pk=token_id, application=self.get_application()
        )
        jeton.revoke()
        logger.info("send_token_revoked", extra={"app": app_id, "token": jeton.id})
        return Response(status=status.HTTP_204_NO_CONTENT)


class WebhookSecretRevealApiView(_OwnedApplicationMixin, APIView):
    """POST {password} : renvoie le secret qui signe les callbacks.

    Le propriétaire doit pouvoir le lire — sans lui, il ne peut pas vérifier la
    signature de son côté. Même exigence que pour un jeton d'émission : une
    session ouverte ne suffit pas à relire un secret.
    """

    serializer_class = RevealSerializer

    def post(self, request, app_id):
        serializer = RevealSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        application = self.get_application()
        if authenticate(request=request, username=request.user.email,
                        password=serializer.validated_data["password"]) is None:
            logger.warning("webhook_secret_reveal_denied", extra={"app": app_id})
            return error_response(
                code="invalid_password",
                detail="Mot de passe incorrect.",
                http_status=status.HTTP_403_FORBIDDEN,
            )

        logger.info("webhook_secret_revealed", extra={"app": application.id})
        return Response({"webhook_secret": application.webhook_secret})


class WebhookSecretRotateApiView(_OwnedApplicationMixin, APIView):
    """POST : tire un nouveau secret de signature.

    Le geste d'urgence si le secret a fuité. Les callbacks suivants sont signés
    avec le nouveau : mettre à jour l'endpoint **d'abord**, sinon ils seront
    rejetés le temps du décalage.
    """

    def post(self, request, app_id):
        application = self.get_application()
        application.rotate_webhook_secret()
        application.save(update_fields=["webhook_secret"])
        logger.info("webhook_secret_rotated", extra={"app": application.id})
        return Response({"app_id": application.id, "webhook_secret": application.webhook_secret})


class SendTokenRevealApiView(_OwnedApplicationMixin, APIView):
    """POST {password} : renvoie la valeur en clair, et le journalise."""

    serializer_class = RevealSerializer

    def post(self, request, app_id, token_id):
        serializer = RevealSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Re-saisie du mot de passe : une session ouverte ne suffit pas a relire
        # un secret. C'est toute la difference entre « relisible par toi » et
        # « relisible par qui a atteint ta session ».
        if authenticate(request=request, username=request.user.email,
                        password=serializer.validated_data["password"]) is None:
            logger.warning("send_token_reveal_denied", extra={"app": app_id, "token": token_id})
            return error_response(
                code="invalid_password",
                detail="Mot de passe incorrect.",
                http_status=status.HTTP_403_FORBIDDEN,
            )

        jeton = generics.get_object_or_404(
            AppSendToken, pk=token_id, application=self.get_application()
        )
        try:
            brut = jeton.reveal()
        except RevealUnavailable as exc:
            # Le jeton reste utilisable -- l'authentification passe par
            # l'empreinte. Seule sa relecture est perdue, et on le dit.
            return error_response(
                code="reveal_unavailable",
                detail=str(exc),
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        SendTokenReveal.objects.create(
            token=jeton, actor=request.user, actor_label=request.user.email
        )
        logger.info("send_token_revealed", extra={"app": app_id, "token": jeton.id})
        return Response({"token": brut})
