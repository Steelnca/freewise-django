from django.urls import path

from .views import (
    WalletView,
    WalletTransactionsView,
    FundMilestoneView,
    PaymentAttemptStatusView,
    MyPayoutsView,
    MyEscrowView,
    RequestPayoutView,
    PaymentGatewayWebhookView,
    RetryPaymentAttemptView,
)

urlpatterns = [
    path("wallet/", WalletView.as_view(), name="wallet-detail"),
    path("transactions/", WalletTransactionsView.as_view(), name="wallet-transactions"),
    path("fund/<int:milestone_id>/", FundMilestoneView.as_view(), name="milestone-fund"),
    path("payouts/", MyPayoutsView.as_view(), name="payout-list"),
    path("payouts/request/", RequestPayoutView.as_view(), name="payout-request"),
    path("escrow/", MyEscrowView.as_view(), name="escrow-list"),
    path("webhooks/<slug:provider_name>/", PaymentGatewayWebhookView.as_view(), name="payment-gateway-webhook"),
    path("attempts/<uuid:attempt_id>/status/", PaymentAttemptStatusView.as_view(), name="payment-attempt-status"),
    path("attempts/<uuid:attempt_id>/retry/", RetryPaymentAttemptView.as_view(), name="payment-attempt-retry"),
]