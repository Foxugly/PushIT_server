"""Endpoints de facturation, adossés au service central.

PushIT ne parle jamais à Stripe : il relaie vers billing-api.foxugly.com, signé
en HMAC, et reçoit en retour des droits poussés qu'il met en cache localement.

Le changement de quantité passe par deux appels distincts — aperçu puis
application — pour que l'utilisateur voie le montant **avant** qu'on le prélève.
"""
import logging

from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import error_response

from . import client
from .models import DeliveryReceipt, Subscription
from .service import application_quota, applications_used, billing_configured, user_is_paid

logger = logging.getLogger("pushit")


def _unconfigured():
    return error_response(
        code="billing_unconfigured", detail="La facturation n'est pas activée.", http_status=503
    )


def _unavailable():
    """Le central est branché mais injoignable : 503 explicite, jamais une 500."""
    return error_response(
        code="billing_unavailable",
        detail="Le service de facturation est momentanément indisponible.",
        http_status=503,
    )


def _refused(refus):
    """Relaie le refus du central tel quel.

    Le code et le detail viennent de lui : c'est lui qui sait pourquoi, et le
    SPA doit pouvoir distinguer « vous avez deja un abonnement » d'une panne.
    """
    return error_response(code=refus.code, detail=refus.detail, http_status=refus.status_code)


def _front(path: str = "") -> str:
    from django.conf import settings

    return f"{settings.FRONTEND_BASE_URL.rstrip('/')}{path}"


def apply_entitlement(user, payload: dict) -> Subscription:
    """Écrit un droit reçu du central dans le cache local."""
    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.is_paid = bool(payload.get("is_paid"))
    sub.status = payload.get("status") or ""
    sub.plan = payload.get("plan") or ""
    sub.interval = payload.get("interval") or ""
    sub.quotas = payload.get("quotas") or {}
    period_end = payload.get("current_period_end")
    sub.current_period_end = parse_datetime(period_end) if period_end else None
    grace = payload.get("grace_until")
    sub.grace_until = parse_datetime(grace) if grace else None
    customer_id = payload.get("stripe_customer_id") or ""
    if customer_id:
        sub.stripe_customer_id = customer_id
    sub.save()
    return sub


class SubscriptionView(APIView):
    """État de facturation du compte, servi depuis le cache local.

    Aucun appel réseau : la page reste rapide et s'affiche même si le central est
    indisponible. Exception au retour du Checkout (`?refresh=1`), où l'on
    interroge le central en synchrone — sinon l'utilisateur qui revient de Stripe
    avant l'arrivée du webhook verrait « aucun abonnement » juste après avoir payé.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.query_params.get("refresh") and billing_configured():
            self._pull(request.user)

        sub = getattr(request.user, "subscription", None)
        return Response(
            {
                "billingEnabled": billing_configured(),
                "isPaid": user_is_paid(request.user),
                "status": sub.status if sub else "",
                "plan": sub.plan if sub else "",
                "interval": sub.interval if sub else "",
                "quota": application_quota(request.user),
                "applicationsUsed": applications_used(request.user),
                "currentPeriodEnd": sub.current_period_end if sub else None,
                "canManage": bool(sub and sub.stripe_customer_id),
            }
        )

    def _pull(self, user):
        from django.conf import settings

        try:
            payload = client.get(f"entitlements/{settings.BILLING_APP_SLUG}/{user.id}/")
        except (client.BillingUnavailable, client.BillingRefused):
            # Panne comme refus, le geste est le meme : servir le cache. Le push
            # finira par arriver, et un rafraichissement de confort ne doit
            # jamais faire echouer l'affichage de la page.
            return
        apply_entitlement(user, payload)


class PlansView(APIView):
    """Le catalogue, relayé depuis le central.

    Les montants ne sont PAS codés en dur dans PushIT : changer un tarif ne doit
    pas exiger un déploiement.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not billing_configured():
            return Response([])
        try:
            return Response(client.get("plans/"))
        except client.BillingRefused as refus:
            return _refused(refus)
        except client.BillingUnavailable:
            return _unavailable()


class CheckoutView(APIView):
    """POST {plan, interval, quantity} → l'URL d'une session Stripe Checkout."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not billing_configured():
            return _unconfigured()
        try:
            data = client.post(
                "checkout/",
                {
                    "external_user_id": str(request.user.id),
                    "email": request.user.email,
                    "plan": request.data.get("plan"),
                    "interval": request.data.get("interval"),
                    "quantity": request.data.get("quantity") or 1,
                    "success_url": _front("/billing?checkout=success"),
                    "cancel_url": _front("/billing?checkout=cancel"),
                },
            )
        except client.BillingRefused as refus:
            return _refused(refus)
        except client.BillingUnavailable:
            return _unavailable()
        return Response({"url": data.get("url", "")})


class PortalView(APIView):
    """POST → l'URL du portail client Stripe (moyens de paiement, factures)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not billing_configured():
            return _unconfigured()
        try:
            data = client.post(
                "portal/",
                {"external_user_id": str(request.user.id), "return_url": _front("/billing")},
            )
        except client.BillingRefused as refus:
            return _refused(refus)
        except client.BillingUnavailable:
            return _unavailable()
        return Response({"url": data.get("url", "")})


class QuantityPreviewView(APIView):
    """POST {quantity} → ce que coûterait le changement, sans rien modifier.

    C'est l'engagement de transparence : on n'ajuste jamais un abonnement sans
    avoir annoncé le prorata, qui n'est pas devinable en cours de période.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not billing_configured():
            return _unconfigured()
        try:
            data = client.post(
                "quantity/preview/",
                {"external_user_id": str(request.user.id), "quantity": request.data.get("quantity")},
            )
        except client.BillingRefused as refus:
            return _refused(refus)
        except client.BillingUnavailable:
            return _unavailable()
        return Response(data)


class QuantityView(APIView):
    """POST {quantity} → applique le changement, avec prorata."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not billing_configured():
            return _unconfigured()
        try:
            data = client.post(
                "quantity/",
                {"external_user_id": str(request.user.id), "quantity": request.data.get("quantity")},
            )
        except client.BillingRefused as refus:
            return _refused(refus)
        except client.BillingUnavailable:
            return _unavailable()
        # Le droit sera recalcule et pousse par le webhook Stripe ; on ne duplique
        # pas ce calcul ici pour eviter deux verites concurrentes.
        logger.info("quantity_change_requested", extra={"user_id": request.user.id})
        return Response(data)


class BillingHistoryView(APIView):
    """Abonnements passés et factures, relayés depuis le central."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not billing_configured():
            return Response({"billingEnabled": False, "subscriptions": [], "invoices": []})
        try:
            data = client.get(f"history/?external_user_id={request.user.id}")
        except (client.BillingUnavailable, client.BillingRefused):
            # La page doit s'afficher meme si le central est coupe ou refuse.
            return Response({"billingEnabled": True, "subscriptions": [], "invoices": []})

        # Le central parle snake_case ; le SPA de la flotte attend du camelCase.
        # La traduction est faite ici plutot que dans le front pour que la forme
        # du central puisse evoluer sans casser deux depots a la fois.
        subscriptions = [
            {
                "id": s.get("id", ""),
                "status": s.get("status", ""),
                "plan": s.get("plan", ""),
                "planName": s.get("plan_name", ""),
                "interval": s.get("interval", ""),
                "quantity": s.get("quantity", 1),
                "startedAt": s.get("started_at"),
                "currentPeriodEnd": s.get("current_period_end"),
                "canceledAt": s.get("canceled_at"),
            }
            for s in data.get("subscriptions", [])
        ]
        invoices = [
            {
                "id": i.get("id", ""),
                "number": i.get("number", ""),
                "status": i.get("status", ""),
                "amountPaid": i.get("amount_paid", 0),
                "currency": i.get("currency", ""),
                "createdAt": i.get("created"),
                "hostedUrl": i.get("hosted_invoice_url", ""),
                "pdfUrl": i.get("invoice_pdf", ""),
            }
            for i in data.get("invoices", [])
        ]
        return Response(
            {"billingEnabled": True, "subscriptions": subscriptions, "invoices": invoices}
        )


class EntitlementView(APIView):
    """Reçoit un droit poussé par le central.

    Signature HMAC obligatoire. L'idempotence repose sur le `delivery_id` : le
    central rejoue volontiers, et un rejeu tardif ne doit pas réappliquer un état
    périmé par-dessus un état plus récent.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        if not billing_configured():
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)

        meta = request.META
        if not client.verify_inbound(
            request.method,
            request._request.get_full_path(),
            request.body,
            meta.get("HTTP_X_FOXUGLY_TIMESTAMP", ""),
            meta.get("HTTP_X_FOXUGLY_SIGNATURE", ""),
        ):
            logger.warning("entitlement_bad_signature")
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        payload = request.data

        if payload.get("ping"):
            # Le central teste la connectivite et le secret depuis sa console.
            # Sans ce cas, un cablage PARFAIT retombe sur « delivery_id requis »
            # (400) et le bouton Test annonce un echec -- ce qui est pire qu'une
            # absence de test, puisqu'on cherche alors une panne inexistante.
            # On repond apres la verification de signature : le test doit
            # prouver le secret, pas seulement la joignabilite.
            return Response({"pong": True}, status=status.HTTP_200_OK)

        delivery_id = payload.get("delivery_id")
        if not delivery_id:
            return error_response(code="missing_delivery_id", detail="delivery_id requis.", http_status=400)

        if DeliveryReceipt.objects.filter(pk=delivery_id).exists():
            # 409 : le central compte cette reponse comme une livraison reussie.
            return Response(status=status.HTTP_409_CONFLICT)

        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(pk=payload.get("external_user_id")).first()
        if user is None:
            # Utilisateur supprime cote PushIT : accuser reception pour que le
            # central cesse de reessayer indefiniment.
            DeliveryReceipt.objects.create(pk=delivery_id)
            logger.info("entitlement_unknown_user", extra={"id": payload.get("external_user_id")})
            return Response(status=status.HTTP_200_OK)

        apply_entitlement(user, payload)
        DeliveryReceipt.objects.create(pk=delivery_id)
        logger.info(
            "entitlement_applied",
            extra={"user_id": user.id, "is_paid": payload.get("is_paid"), "quotas": payload.get("quotas")},
        )
        return Response(status=status.HTTP_200_OK)
