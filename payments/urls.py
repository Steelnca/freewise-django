
from django.urls import path
from .views import FundMilestoneView, ChargilyWebhookView, MyPayoutsView, MyEscrowView

urlpatterns = [
    path('fund/<int:milestone_id>/', FundMilestoneView.as_view(),    name='milestone-fund'),
    path('webhook/',                 ChargilyWebhookView.as_view(),  name='chargily-webhook'),
    path('payouts/',                 MyPayoutsView.as_view(),        name='payout-list'),
    path('escrow/',                  MyEscrowView.as_view(),         name='escrow-list'),
]