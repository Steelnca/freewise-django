from django.urls import path

from .views import (
    ContractListView,
    ContractDetailView,
    ContractDetailView,
    ContractListView,
    MilestonePlanApproveView,
    MilestonePlanCreateView,
    MilestonePlanDetailView,
    MilestoneSubmissionCreateView,
    RequestRevisionView,
    ApproveMilestoneView,
    DisputeMilestoneView,
    CancelContractView,
    MilestoneDeliverableRedirectView,
    ContractEventsView,
)

app_name = "contracts"


urlpatterns = [
    path("", ContractListView.as_view(), name="contract-list"),

    path("proposals/<str:proposal_public_id>/milestone-plans/", MilestonePlanCreateView.as_view(), name="milestone-plan-create"),
    path("milestone-plans/<str:public_id>/", MilestonePlanDetailView.as_view(), name="milestone-plan-detail"),
    path("milestone-plans/<str:public_id>/approve/", MilestonePlanApproveView.as_view(), name="milestone-plan-approve"),

    path("milestones/<str:public_id>/submit/", MilestoneSubmissionCreateView.as_view(), name="milestone-submit"),
    path("milestones/<str:public_id>/request-revision/", RequestRevisionView.as_view(), name="contract-milestone-request-revision"),
    path("milestones/<str:public_id>/approve/", ApproveMilestoneView.as_view(), name="contract-milestone-approve"),
    path("milestones/<str:public_id>/dispute/", DisputeMilestoneView.as_view(), name="contract-milestone-dispute"),
    path("milestones/<str:public_id>/deliverable/", MilestoneDeliverableRedirectView.as_view(), name="contract-milestone-deliverable"),

    path("<str:public_id>/events/", ContractEventsView.as_view(), name="contract-events"),
    path("<str:public_id>/cancel/", CancelContractView.as_view(), name="contract-cancel"),
    path("<str:public_id>/", ContractDetailView.as_view(), name="contract-detail"),
]