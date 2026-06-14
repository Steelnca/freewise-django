from django.urls import path

from .views import (
    SubmitProposalView,
    MyProposalsView,
    JobProposalsView,
    AcceptProposalView,
    WithdrawProposalView,
)

urlpatterns = [
    path("mine/", MyProposalsView.as_view(), name="proposal-mine"),
    path("job/<str:job_public_id>/", JobProposalsView.as_view(), name="job-proposals"),
    path("<str:job_public_id>/submit/", SubmitProposalView.as_view(), name="proposal-submit"),
    path("<str:public_id>/accept/", AcceptProposalView.as_view(), name="proposal-accept"),
    path("<str:public_id>/withdraw/", WithdrawProposalView.as_view(), name="proposal-withdraw"),
]