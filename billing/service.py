"""Gating local, alimenté par le cache de droits.

PushIT ne calcule rien : il lit ce que le service central lui a poussé. Le seul
quota est le **nombre d'applications** qu'un utilisateur peut posséder.

Tant que la facturation n'est pas branchée (`BILLING_*` absents), tout est ouvert.
C'est ce qui permet de déployer cette migration sans rien changer au comportement.

Deux portes s'ouvrent donc sans Stripe : la facturation non branchée, et l'accès
offert (`subscription_bypass`) accordé compte par compte par le staff.
"""
from django.conf import settings
from django.utils import timezone

# Quota "illimité" : valeur haute plutôt qu'un None, pour que les comparaisons
# numériques des appelants restent valides sans cas particulier.
UNLIMITED = 10_000

QUOTA_KEY = "applications"


def billing_configured() -> bool:
    """Vrai quand PushIT sait où joindre le central et avec quel secret."""
    return bool(settings.BILLING_BASE_URL and settings.BILLING_APP_SECRET)


def _active_subscription(user):
    """L'abonnement local s'il ouvre encore des droits.

    `is_paid` vient du central et intègre déjà l'essai et la période de grâce. On
    revérifie `current_period_end` : si le central se tait longtemps (panne,
    livraison définitivement perdue), le cache expire tout seul plutôt que
    d'ouvrir l'accès indéfiniment.
    """
    sub = getattr(user, "subscription", None)
    if sub is None or not sub.is_paid:
        return None
    if sub.current_period_end is not None and sub.current_period_end < timezone.now():
        if sub.grace_until is None or sub.grace_until < timezone.now():
            return None
    return sub


def user_is_paid(user) -> bool:
    """Si l'utilisateur a un abonnement (ou un essai) en cours.

    Un compte offert (`subscription_bypass`) passe toujours : c'est un geste
    commercial accorde par le staff, il ne depend d'aucun abonnement Stripe.
    """
    if not billing_configured():
        return True
    if getattr(user, "subscription_bypass", False):
        return True
    return _active_subscription(user) is not None


def application_quota(user) -> int:
    """Nombre d'applications que l'utilisateur peut posséder.

    Le quota vient du central : pour le plan à l'unité il vaut la quantité
    souscrite, pour le forfait il vaut « illimité ».
    """
    if not billing_configured():
        return UNLIMITED
    if getattr(user, "subscription_bypass", False):
        return UNLIMITED
    sub = _active_subscription(user)
    if sub is None:
        return 0
    quota = (sub.quotas or {}).get(QUOTA_KEY)
    return quota if isinstance(quota, int) else 0


def applications_used(user) -> int:
    from applications.models import Application

    return Application.objects.filter(owner=user).count()


def quota_required(user):
    """402 si l'utilisateur ne peut pas créer une application de plus, sinon None.

    Deux refus distincts, parce qu'ils appellent deux gestes différents : souscrire,
    ou augmenter la quantité déjà souscrite. Une erreur unique laisserait
    l'utilisateur deviner lequel.
    """
    if not billing_configured():
        return None

    from config.api_errors import error_response

    if not user_is_paid(user):
        return error_response(
            code="subscription_required",
            detail="Un abonnement est nécessaire pour créer une application.",
            http_status=402,
        )

    quota = application_quota(user)
    if applications_used(user) >= quota:
        return error_response(
            code="quota_exceeded",
            detail=(
                f"Votre abonnement couvre {quota} application(s). "
                "Augmentez la quantité souscrite pour en créer une de plus."
            ),
            http_status=402,
        )
    return None
