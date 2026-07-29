import logging
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone
from rest_framework import authentication, exceptions

from config.metrics import increment_counter
from .models import Application

logger = logging.getLogger("pushit")


@dataclass(frozen=True)
class AppTokenPrincipal:
    pk: str
    application_id: int
    owner_id: int
    auth_kind: str = "application"

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_staff(self):
        return False

    @property
    def is_superuser(self):
        return False

    @property
    def is_app_token_principal(self):
        return True

    def get_username(self):
        return self.pk

    def __str__(self):
        return self.pk

    def __getattr__(self, name):
        raise AttributeError(
            f"AppTokenPrincipal does not expose '{name}'. "
            "Use request.auth_application for application data "
            "and do not treat app-token auth as a human user."
        )


class AppTokenAuthentication(authentication.BaseAuthentication):
    header_name = "HTTP_X_APP_TOKEN"

    def authenticate(self, request):
        raw_token = request.META.get(self.header_name)
        application = get_application_for_raw_app_token(raw_token)

        request.auth_application = application
        principal = AppTokenPrincipal(
            pk=f"app:{application.id}",
            application_id=application.id,
            owner_id=application.owner_id,
        )
        increment_counter("pushit_app_token_auth_total", labels={"outcome": "success"})
        return (principal, application)

    def authenticate_header(self, request):
        return "X-App-Token"


def get_application_for_enrolment(raw_code: str | None) -> Application:
    """Résout un identifiant présenté pour **rattacher un terminal**.

    Volontairement séparée de `get_application_for_raw_app_token`, qui sert à
    l'**émission**. Les deux usages n'acceptent pas les mêmes identifiants, et
    les confondre est exactement le défaut qu'on corrige : un code d'enrôlement
    circule vers chaque destinataire, il ne doit jamais ouvrir un envoi.

    Accepte le code d'enrôlement, et — le temps de la transition — le jeton
    hérité que portent les installations mobiles déjà publiées.
    """
    if not raw_code:
        increment_counter("pushit_app_token_auth_total", labels={"outcome": "missing"})
        raise exceptions.NotAuthenticated("Missing app token.", code="app_token_missing")

    if raw_code.startswith("apk_"):
        application = Application.objects.select_related("owner").filter(
            enrolment_code=raw_code
        ).first()
        if application is None:
            increment_counter("pushit_app_token_auth_total", labels={"outcome": "invalid"})
            raise exceptions.AuthenticationFailed("Invalid app token.", code="app_token_invalid")
        if not application.is_active:
            increment_counter("pushit_app_token_auth_total", labels={"outcome": "inactive"})
            raise exceptions.AuthenticationFailed(
                "Inactive application.", code="app_token_inactive"
            )
        increment_counter("pushit_app_token_auth_total", labels={"outcome": "enrolment_code"})
        return application

    # Repli hérité, et hérité SEULEMENT : surtout pas via le résolveur
    # d'émission, qui accepte maintenant les jetons `apt_` dédiés. Y retomber
    # laisserait un jeton d'émission rattacher un terminal — donc le
    # redistribuer aux téléphones, et recréer le défaut qu'on corrige.
    application = _legacy_application(raw_code)

    # LA mesure qui conditionne la suppression des colonnes `app_token_*`
    # (BACKLOG B3). Jusqu'ici, seuls les ECHECS de ce chemin étaient comptés :
    # un enrôlement hérité réussi ne laissait aucune trace, donc la condition
    # « plus aucun terminal n'en dépend » ne pouvait jamais être déclarée vraie.
    # Un backlog dont la condition n'est pas mesurable n'est pas un backlog,
    # c'est un vœu.
    increment_counter("pushit_app_token_auth_total", labels={"outcome": "legacy_enrolment"})
    logger.info("legacy_enrolment_used", extra={"app": application.id})
    return application


def get_application_for_raw_app_token(raw_token: str | None) -> Application:
    """Validate a raw app token and return its application without changing request.user.

    Chemin d'**émission** : n'accepte QUE le jeton `apt_`. Un code d'enrôlement
    présenté ici doit échouer — c'est ce qui empêche un destinataire d'écrire aux
    autres. Le format est vérifié avant toute recherche, donc un `apk_` sort ici.
    """

    if not raw_token:
        increment_counter("pushit_app_token_auth_total", labels={"outcome": "missing"})
        raise exceptions.NotAuthenticated(
            "Missing app token.",
            code="app_token_missing",
        )

    if not raw_token.startswith("apt_"):
        increment_counter("pushit_app_token_auth_total", labels={"outcome": "invalid_format"})
        raise exceptions.AuthenticationFailed(
            "Invalid app token format.",
            code="app_token_invalid_format",
        )

    # D'abord les jetons d'emission dedies. Le jeton historique n'est plus qu'un
    # repli, compte pour savoir quand plus personne ne l'utilise -- c'est la
    # condition d'extinction.
    from .models_send_token import AppSendToken

    jeton = AppSendToken.objects.select_related("application__owner").filter(
        token_hash=AppSendToken.hash_raw(raw_token)
    ).first()
    if jeton is not None:
        if jeton.revoked_at is not None:
            increment_counter("pushit_app_token_auth_total", labels={"outcome": "revoked"})
            raise exceptions.AuthenticationFailed(
                "Revoked app token.", code="app_token_revoked"
            )
        application = jeton.application
        if not application.is_active:
            increment_counter("pushit_app_token_auth_total", labels={"outcome": "inactive"})
            raise exceptions.AuthenticationFailed(
                "Inactive application.", code="app_token_inactive"
            )
        AppSendToken.objects.filter(pk=jeton.pk).update(last_used_at=timezone.now())
        Application.objects.filter(pk=application.pk).update(last_used_at=timezone.now())
        return application

    # ETEINT (2026-07-29). Le jeton historique n'emet plus. C'etait la derniere
    # porte par laquelle un destinataire -- qui detient forcement ce jeton, il
    # etait dans le QR -- pouvait ecrire a tous les autres terminaux.
    #
    # Il reste accepte a l'ENROLEMENT (`get_application_for_enrolment`) : des
    # installations mobiles le portent encore et s'en servent pour se rattacher.
    # Le refuser la les couperait sans que personne y gagne quoi que ce soit.
    #
    # Code distinct d'`app_token_invalid` a dessein : un 401 muet enverrait
    # chercher un probleme d'authentification, alors que la reponse est
    # « ce jeton n'emet plus, cree un jeton d'emission ».
    increment_counter("pushit_app_token_auth_total", labels={"outcome": "legacy_send_refused"})
    raise exceptions.AuthenticationFailed(
        "This application token no longer authorises sending. "
        "Create a send token from the console and use it instead.",
        code="app_token_retired",
    )


def _legacy_application(raw_token: str) -> Application:
    """Résout le jeton historique, et lui seul.

    Partagé par les deux chemins pendant la transition. Séparé du résolveur
    d'émission exprès : celui-ci accepte les jetons `apt_` dédiés, qui ne
    doivent jamais servir à enrôler.
    """
    if not raw_token.startswith("apt_"):
        increment_counter("pushit_app_token_auth_total", labels={"outcome": "invalid_format"})
        raise exceptions.AuthenticationFailed(
            "Invalid app token format.",
            code="app_token_invalid_format",
        )

    token_hash = Application.hash_app_token(raw_token)

    try:
        application = Application.objects.select_related("owner").get(app_token_hash=token_hash)
    except Application.DoesNotExist:
        increment_counter("pushit_app_token_auth_total", labels={"outcome": "invalid"})
        raise exceptions.AuthenticationFailed(
            "Invalid app token.",
            code="app_token_invalid",
        )
    except Application.MultipleObjectsReturned:
        # Two applications share this token hash — a data-integrity violation
        # (app_token_hash is declared unique). Fail closed: never authenticate an
        # ambiguous token, which could otherwise grant cross-application access.
        # The counter surfaces it for monitoring; the rows must be fixed manually.
        increment_counter("pushit_app_token_auth_total", labels={"outcome": "duplicate"})
        raise exceptions.AuthenticationFailed(
            "Invalid app token.",
            code="app_token_invalid",
        )

    if not application.is_active:
        increment_counter("pushit_app_token_auth_total", labels={"outcome": "inactive"})
        raise exceptions.AuthenticationFailed(
            "Inactive application.",
            code="app_token_inactive",
        )

    if application.revoked_at is not None:
        increment_counter("pushit_app_token_auth_total", labels={"outcome": "revoked"})
        raise exceptions.AuthenticationFailed(
            "App token has been revoked.",
            code="app_token_revoked",
        )

    now = timezone.now()
    if not application.last_used_at or application.last_used_at < now - timedelta(minutes=5):
        application.last_used_at = now
        application.save(update_fields=["last_used_at"])

    return application
