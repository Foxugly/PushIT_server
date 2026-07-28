from django.urls import path

from .api_views import (
    BillingHistoryView,
    CheckoutView,
    EntitlementView,
    PlansView,
    PortalView,
    QuantityPreviewView,
    QuantityView,
    SubscriptionView,
)


urlpatterns = [
    path("subscription/", SubscriptionView.as_view(), name="billing-subscription"),
    path("plans/", PlansView.as_view(), name="billing-plans"),
    path("history/", BillingHistoryView.as_view(), name="billing-history"),
    path("checkout/", CheckoutView.as_view(), name="billing-checkout"),
    path("portal/", PortalView.as_view(), name="billing-portal"),
    path("quantity/preview/", QuantityPreviewView.as_view(), name="billing-quantity-preview"),
    path("quantity/", QuantityView.as_view(), name="billing-quantity"),
    # Recoit les droits pousses par le central, signes en HMAC.
    path("entitlement/", EntitlementView.as_view(), name="billing-entitlement"),
]
