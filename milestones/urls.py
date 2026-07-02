from django.urls import path

from .views import (
    MilestonePlanApproveView,
    MilestonePlanCreateView,
    MilestonePlanDetailView,
    MilestoneSubmissionCreateView,
    MilestoneRevisionView,
    MilestoneApproveView,
    MilestoneDisputeView,
    MilestoneDeliverableRedirectView,
    MilestoneTemplateSuggestView,
)

app_name = "milestones"


urlpatterns = [

    path("jobs/<str:job_public_id>/milestone-plans/", MilestonePlanCreateView.as_view(), name="milestone-plan-create"),
    path("milestone-plans/<str:public_id>/", MilestonePlanDetailView.as_view(), name="milestone-plan-detail"),
    path("milestone-plans/<str:public_id>/approve/", MilestonePlanApproveView.as_view(), name="milestone-plan-approve"),

    path("<str:public_id>/submit/", MilestoneSubmissionCreateView.as_view(), name="milestone-submit"),
    path("<str:public_id>/request-revision/", MilestoneRevisionView.as_view(), name="milestone-request-revision"),
    path("<str:public_id>/approve/", MilestoneApproveView.as_view(), name="milestone-approve"),
    path("<str:public_id>/dispute/", MilestoneDisputeView.as_view(), name="milestone-dispute"),
    path("<str:public_id>/deliverable/", MilestoneDeliverableRedirectView.as_view(), name="milestone-deliverable"),

    path("templates/suggest/", MilestoneTemplateSuggestView.as_view(), name="milestone-template-suggest"),
]