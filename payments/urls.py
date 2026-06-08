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
    RetryFundMilestoneView,
    MilestoneLatestAttemptStatusView
)

urlpatterns = [
    path("wallet/", WalletView.as_view(), name="wallet-detail"),
    path("transactions/", WalletTransactionsView.as_view(), name="wallet-transactions"),
    path("milestones/<str:milestone_public_id>/fund/", FundMilestoneView.as_view(), name="milestone-fund"),
    path("milestones/<str:milestone_public_id>/retry/", RetryFundMilestoneView.as_view(), name="milestone-retry"),
    path("payouts/", MyPayoutsView.as_view(), name="payout-list"),
    path("payouts/request/", RequestPayoutView.as_view(), name="payout-request"),
    path("escrow/", MyEscrowView.as_view(), name="escrow-list"),
    path("webhooks/<slug:provider_name>/", PaymentGatewayWebhookView.as_view(), name="payment-gateway-webhook"),
    path("attempts/<uuid:attempt_id>/status/", PaymentAttemptStatusView.as_view(), name="payment-attempt-status"),
    path("milestones/<str:milestone_public_id>/attempt-status/", MilestoneLatestAttemptStatusView.as_view(), name="milestone-latest-attempt-status"),
]