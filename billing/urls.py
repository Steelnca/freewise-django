from django.urls import path

from .views import (
    ActivateClientSubscriptionView,
    ActivateFreelancerSubscriptionView,
    MyClientSubscriptionView,
    MyFreelancerSubscriptionView,
    MyQuotaView,
    SubscriptionPlanDetailView,
    SubscriptionPlanListView,
)

urlpatterns = [
    path("plans/", SubscriptionPlanListView.as_view(), name="billing-plans"),
    path("plans/<str:public_id>/", SubscriptionPlanDetailView.as_view(), name="billing-plan-detail"),
    path("me/freelancer-subscription/", MyFreelancerSubscriptionView.as_view(), name="billing-my-freelancer-subscription"),
    path("me/freelancer-subscription/activate/", ActivateFreelancerSubscriptionView.as_view(), name="billing-freelancer-subscription-activate"),
    path("me/client-subscription/", MyClientSubscriptionView.as_view(), name="billing-my-client-subscription"),
    path("me/client-subscription/activate/", ActivateClientSubscriptionView.as_view(), name="billing-client-subscription-activate"),
    path("me/quota/", MyQuotaView.as_view(), name="billing-my-quota"),
]