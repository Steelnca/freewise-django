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
    ContractEventsView,
)

urlpatterns = [
    path("", MyContractsView.as_view(), name="contract-list"),
    path("<str:public_id>/", ContractDetailView.as_view(), name="contract-detail"),
    path("<str:public_id>/cancel/", CancelContractView.as_view(), name="contract-cancel"),
    path("<str:public_id>/milestones/", CreateMilestoneView.as_view(), name="contract-milestone-create"),
    path("milestones/<str:public_id>/submit/", SubmitMilestoneView.as_view(), name="contract-milestone-submit"),
    path("milestones/<str:public_id>/request-revision/", RequestRevisionView.as_view(), name="contract-milestone-request-revision"),
    path("milestones/<str:public_id>/approve/", ApproveMilestoneView.as_view(), name="contract-milestone-approve"),
    path("milestones/<str:public_id>/dispute/", DisputeMilestoneView.as_view(), name="contract-milestone-dispute"),
    path("milestones/<str:public_id>/deliverable/", MilestoneDeliverableRedirectView.as_view(), name="contract-milestone-deliverable"),
    path("<str:public_id>/events/", ContractEventsView.as_view(), name="contract-events"),
]