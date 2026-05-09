
from django.urls import path
from .views import (
    MyContractsView, ContractDetailView,
    SubmitMilestoneView, ApproveMilestoneView, DisputeMilestoneView,
)

urlpatterns = [
    path('',                                  MyContractsView.as_view(),     name='contract-list'),
    path('<int:pk>/',                         ContractDetailView.as_view(),  name='contract-detail'),
    path('milestones/<int:pk>/submit/',       SubmitMilestoneView.as_view(), name='milestone-submit'),
    path('milestones/<int:pk>/approve/',      ApproveMilestoneView.as_view(),name='milestone-approve'),
    path('milestones/<int:pk>/dispute/',      DisputeMilestoneView.as_view(),name='milestone-dispute'),
]