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
    path("job/<int:job_id>/", JobProposalsView.as_view(), name="job-proposals"),
    path("<int:job_id>/submit/", SubmitProposalView.as_view(), name="proposal-submit"),
    path("<int:proposal_id>/accept/", AcceptProposalView.as_view(), name="proposal-accept"),
    path("<int:proposal_id>/withdraw/", WithdrawProposalView.as_view(), name="proposal-withdraw"),
]