# payments/urls.py
from django.urls import path

from .views import (
    WalletView,
    WalletTransactionsView,
    FundMilestoneView,
    ChargilyWebhookView,
    MyPayoutsView,
    MyEscrowView,
    RequestPayoutView,
)

urlpatterns = [
    path("wallet/", WalletView.as_view(), name="wallet-detail"),
    path("transactions/", WalletTransactionsView.as_view(), name="wallet-transactions"),
    path("fund/<int:milestone_id>/", FundMilestoneView.as_view(), name="milestone-fund"),
    path("webhook/", ChargilyWebhookView.as_view(), name="chargily-webhook"),
    path("payouts/", MyPayoutsView.as_view(), name="payout-list"),
    path("payouts/request/", RequestPayoutView.as_view(), name="payout-request"),
    path("escrow/", MyEscrowView.as_view(), name="escrow-list"),
]