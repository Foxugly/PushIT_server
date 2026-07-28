"""Gating de facturation de PushIT, adossé au service central.

Le seul quota est le **nombre d'applications**. Il vient du central : pour le plan
à l'unité il vaut la quantité souscrite, pour le forfait il vaut « illimité ».

Inerte tant que les variables `BILLING_*` sont absentes — c'est ce qui permet de
déployer sans rien changer au comportement.
"""
import json
import time
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from applications.models import Application
from billing.client import BillingUnavailable, _sign
from billing.models import DeliveryReceipt, Subscription
from billing.service import application_quota, billing_configured, user_is_paid

User = get_user_model()

BILLING_ON = {
    "BILLING_BASE_URL": "https://billing-api.foxugly.com",
    "BILLING_APP_SECRET": "secret-de-test",
    "BILLING_APP_SLUG": "pushit",
}


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="o@example.com", password="pw12345678")


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _abonne(user, quota=1, **kwargs):
    """Le cache local tel que le central l'aurait rempli."""
    defaults = {"is_paid": True, "status": "active", "plan": "app", "quotas": {"applications": quota}}
    defaults.update(kwargs)
    return Subscription.objects.create(user=user, **defaults)


def _creer_app(user, nom="A"):
    return _client(user).post("/api/v1/apps/", {"name": nom}, format="json")


# ------------------------------------------------------------------ mode inerte

@pytest.mark.django_db
def test_unconfigured_billing_is_inert(owner):
    """C'est ce qui permet de déployer la migration sans rien changer en prod."""
    assert billing_configured() is False
    assert user_is_paid(owner) is True
    assert application_quota(owner) >= 1
    assert _creer_app(owner).status_code == 201


@pytest.mark.django_db
def test_checkout_and_portal_503_when_unconfigured(owner):
    c = _client(owner)
    assert c.post("/api/v1/billing/checkout/", {"plan": "app", "interval": "monthly"}, format="json").status_code == 503
    assert c.post("/api/v1/billing/portal/", {}, format="json").status_code == 503


# ------------------------------------------------------------------------ gating

@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_without_a_subscription_no_application_can_be_created(owner):
    assert user_is_paid(owner) is False
    assert application_quota(owner) == 0

    r = _creer_app(owner)

    assert r.status_code == 402
    assert r.json()["code"] == "subscription_required"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_the_quota_follows_the_subscribed_quantity(owner):
    """Payer pour trois applications doit en débloquer trois, pas une."""
    _abonne(owner, quota=3)

    assert application_quota(owner) == 3
    assert _creer_app(owner, "A").status_code == 201
    assert _creer_app(owner, "B").status_code == 201
    assert _creer_app(owner, "C").status_code == 201

    r = _creer_app(owner, "D")
    assert r.status_code == 402
    assert r.json()["code"] == "quota_exceeded"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_the_two_refusals_are_distinct(owner):
    """« Abonnez-vous » et « augmentez votre quantité » appellent deux gestes
    différents : une erreur unique laisserait l'utilisateur deviner lequel."""
    sans = _creer_app(owner).json()["code"]
    _abonne(owner, quota=1)
    _creer_app(owner, "A")
    avec = _creer_app(owner, "B").json()["code"]

    assert sans == "subscription_required"
    assert avec == "quota_exceeded"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_the_unlimited_plan_never_blocks(owner):
    _abonne(owner, quota=10000, plan="unlimited")

    for i in range(12):
        assert _creer_app(owner, f"A{i}").status_code == 201


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_trialing_subscription_grants_access(owner):
    """L'essai doit ouvrir l'accès, sinon le premier mois offert ne sert à rien."""
    _abonne(owner, quota=1, status="trialing")

    assert user_is_paid(owner) is True
    assert _creer_app(owner).status_code == 201


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_an_unpaid_subscription_grants_nothing(owner):
    """is_paid vient du central : PushIT ne réinterprète aucun statut Stripe."""
    _abonne(owner, quota=5, is_paid=False, status="past_due")

    assert user_is_paid(owner) is False
    assert application_quota(owner) == 0


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_an_expired_cache_closes_access_if_the_central_goes_silent(owner):
    """Filet : si le central se tait durablement, le cache expire au lieu
    d'ouvrir l'accès indéfiniment."""
    _abonne(owner, quota=3, current_period_end=timezone.now() - timezone.timedelta(days=1))

    assert user_is_paid(owner) is False


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_reducing_the_quota_below_the_apps_already_created_blocks_creation(owner):
    """Rétrograder de 3 à 1 ne détruit aucune application existante — mais on ne
    peut plus en créer. Détruire des données du client serait inacceptable."""
    _abonne(owner, quota=3)
    for n in "ABC":
        _creer_app(owner, n)

    Subscription.objects.filter(user=owner).update(quotas={"applications": 1})

    assert Application.objects.filter(owner=owner).count() == 3
    assert _creer_app(owner, "D").status_code == 402


# ------------------------------------------------- réception d'un droit poussé

def _payload(user, **kwargs):
    base = {
        "delivery_id": str(uuid.uuid4()),
        "app": "pushit",
        "external_user_id": str(user.id),
        "is_paid": True,
        "status": "active",
        "plan": "app",
        "interval": "monthly",
        "quotas": {"applications": 4},
        "current_period_end": None,
        "grace_until": None,
        "stripe_customer_id": "cus_123",
        "source": "stripe",
    }
    base.update(kwargs)
    return base


def _push(payload, secret="secret-de-test", timestamp=None):
    body = json.dumps(payload).encode()
    ts = timestamp if timestamp is not None else int(time.time())
    with override_settings(**{**BILLING_ON, "BILLING_APP_SECRET": secret}):
        signature = _sign("POST", "/api/v1/billing/entitlement/", body, ts)
    return APIClient().post(
        "/api/v1/billing/entitlement/",
        data=body,
        content_type="application/json",
        HTTP_X_FOXUGLY_TIMESTAMP=str(ts),
        HTTP_X_FOXUGLY_SIGNATURE=signature,
    )


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_signed_push_updates_the_local_cache(owner):
    r = _push(_payload(owner))

    assert r.status_code == 200
    sub = Subscription.objects.get(user=owner)
    assert sub.is_paid is True and sub.quotas == {"applications": 4}
    assert application_quota(owner) == 4


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_an_unsigned_push_is_refused(owner):
    r = APIClient().post("/api/v1/billing/entitlement/", _payload(owner), format="json")

    assert r.status_code == 401
    assert Subscription.objects.count() == 0


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_push_signed_with_the_wrong_secret_is_refused(owner):
    assert _push(_payload(owner), secret="mauvais-secret").status_code == 401


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_stale_push_is_refused(owner):
    assert _push(_payload(owner), timestamp=int(time.time()) - 400).status_code == 401


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_replaying_a_delivery_is_a_no_op(owner):
    """Un rejeu tardif ne doit pas réappliquer un état périmé."""
    payload = _payload(owner)
    assert _push(payload).status_code == 200

    Subscription.objects.filter(user=owner).update(is_paid=False, quotas={})
    r = _push(payload)

    assert r.status_code == 409
    assert Subscription.objects.get(user=owner).is_paid is False


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_push_for_an_unknown_user_is_acknowledged(owner):
    """Sinon le central réessaierait indéfiniment pour un compte supprimé."""
    r = _push(_payload(owner, external_user_id="999999"))

    assert r.status_code == 200
    assert Subscription.objects.count() == 0
    assert DeliveryReceipt.objects.count() == 1


# ----------------------------------------------------- proxys vers le central

@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_checkout_forwards_the_quantity(owner):
    """Sans elle, un client qui paie pour cinq applications n'en obtiendrait qu'une."""
    with patch("billing.client.post", return_value={"url": "https://checkout.stripe.com/c/x"}) as envoye:
        r = _client(owner).post(
            "/api/v1/billing/checkout/",
            {"plan": "app", "interval": "monthly", "quantity": 5},
            format="json",
        )

    assert r.status_code == 200
    assert envoye.call_args.args[1]["quantity"] == 5


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_the_quantity_preview_changes_nothing(owner):
    """C'est l'engagement de transparence : annoncer avant de prélever."""
    apercu = {"current_quantity": 2, "new_quantity": 4, "amount_due_now": 247, "currency": "EUR"}
    with patch("billing.client.post", return_value=apercu) as envoye:
        r = _client(owner).post("/api/v1/billing/quantity/preview/", {"quantity": 4}, format="json")

    assert r.status_code == 200
    assert r.json()["amount_due_now"] == 247
    assert envoye.call_args.args[0] == "quantity/preview/"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_an_unreachable_central_yields_503_not_500(owner):
    with patch("billing.client.post", side_effect=BillingUnavailable("timeout")):
        r = _client(owner).post(
            "/api/v1/billing/checkout/", {"plan": "app", "interval": "monthly"}, format="json"
        )

    assert r.status_code == 503
    assert r.json()["code"] == "billing_unavailable"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_the_status_endpoint_never_calls_the_network_without_refresh(owner):
    _abonne(owner, quota=3)

    with patch("billing.client.get") as appele:
        body = _client(owner).get("/api/v1/billing/subscription/").json()

    appele.assert_not_called()
    assert body["quota"] == 3 and body["isPaid"] is True


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_refresh_pulls_the_central_for_the_checkout_return(owner):
    """Sans ce pull, l'utilisateur revenant de Stripe avant le webhook verrait
    « aucun abonnement » juste après avoir payé."""
    with patch("billing.client.get", return_value=_payload(owner)) as tire:
        body = _client(owner).get("/api/v1/billing/subscription/?refresh=1").json()

    tire.assert_called_once()
    assert body["isPaid"] is True and body["quota"] == 4


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_plans_are_relayed_not_hardcoded(owner):
    """Changer un tarif ne doit pas exiger un déploiement de PushIT."""
    with patch("billing.client.get", return_value=[{"code": "app"}]) as appele:
        body = _client(owner).get("/api/v1/billing/plans/").json()

    assert body == [{"code": "app"}]
    assert appele.call_args.args[0] == "plans/"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_the_history_is_translated_into_the_fleet_shape(owner):
    """Le central parle snake_case, le SPA attend du camelCase : la traduction
    vit ici pour que la forme du central puisse bouger sans casser le front."""
    brut = {
        "subscriptions": [{
            "id": "sub_1", "status": "active", "plan": "app", "plan_name": "Par application",
            "interval": "monthly", "quantity": 3,
            "started_at": "2026-01-01T00:00:00Z", "current_period_end": "2026-02-01T00:00:00Z",
            "canceled_at": None,
        }],
        "invoices": [{
            "id": "in_1", "number": "F-001", "status": "paid", "amount_paid": 726,
            "currency": "EUR", "created": "2026-01-01T00:00:00Z",
            "hosted_invoice_url": "https://invoice", "invoice_pdf": "https://pdf",
        }],
    }
    with patch("billing.client.get", return_value=brut):
        body = _client(owner).get("/api/v1/billing/history/").json()

    assert body["subscriptions"][0]["planName"] == "Par application"
    assert body["subscriptions"][0]["quantity"] == 3
    assert body["subscriptions"][0]["currentPeriodEnd"] == "2026-02-01T00:00:00Z"
    assert body["invoices"][0]["amountPaid"] == 726
    assert body["invoices"][0]["pdfUrl"] == "https://pdf"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_an_unreachable_central_still_renders_the_history_page(owner):
    """Une page de facturation blanche inquiete plus qu'une page vide."""
    with patch("billing.client.get", side_effect=BillingUnavailable("timeout")):
        r = _client(owner).get("/api/v1/billing/history/")

    assert r.status_code == 200
    assert r.json() == {"billingEnabled": True, "subscriptions": [], "invoices": []}
