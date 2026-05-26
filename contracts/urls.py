from django.urls import path

from .views import (
    MyContractsView,
    ContractDetailView,
    SubmitMilestoneView,
    RequestRevisionView,
    ApproveMilestoneView,
    DisputeMilestoneView,
    CancelContractView,
)

urlpatterns = [
    path("", MyContractsView.as_view(), name="contract-list"),
    path("<int:pk>/", ContractDetailView.as_view(), name="contract-detail"),
    path("milestones/<int:pk>/submit/", SubmitMilestoneView.as_view(), name="milestone-submit"),
    path(
        "milestones/<int:pk>/request-revision/",
        RequestRevisionView.as_view(),
        name="milestone-request-revision",
    ),
    path("milestones/<int:pk>/approve/", ApproveMilestoneView.as_view(), name="milestone-approve"),
    path("milestones/<int:pk>/dispute/", DisputeMilestoneView.as_view(), name="milestone-dispute"),
    path("<int:pk>/cancel/", CancelContractView.as_view(), name="contract-cancel"),
]