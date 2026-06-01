from django.urls import path

from .views import (
    MyContractsView,
    CreateMilestoneView,
    ContractDetailView,
    SubmitMilestoneView,
    RequestRevisionView,
    ApproveMilestoneView,
    DisputeMilestoneView,
    CancelContractView,
    MilestoneDeliverableRedirectView,
)

urlpatterns = [
    path("", MyContractsView.as_view(), name="contract-list"),
    path("<int:pk>/", ContractDetailView.as_view(), name="contract-detail"),
    path("<int:pk>/cancel/", CancelContractView.as_view(), name="contract-cancel"),
    path("<int:pk>/milestones/", CreateMilestoneView.as_view(), name="contract-milestone-create"),
    path("milestones/<int:pk>/submit/", SubmitMilestoneView.as_view(), name="contract-milestone-submit"),
    path("milestones/<int:pk>/request-revision/", RequestRevisionView.as_view(), name="milestone-request-revision"),
    path("milestones/<int:pk>/approve/", ApproveMilestoneView.as_view(), name="contract-milestone-approve"),
    path("milestones/<int:pk>/dispute/", DisputeMilestoneView.as_view(), name="contract-milestone-dispute"),
    path("milestones/<int:pk>/deliverable/", MilestoneDeliverableRedirectView.as_view(), name="contract-milestone-deliverable"),
]