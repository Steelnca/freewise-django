from django.urls import path

from .views import (

    MyProposalsView,
    AcceptProposalView,
    WithdrawProposalView,
    RejectProposalView,
)

urlpatterns = [
    path("mine/", MyProposalsView.as_view(), name="proposal-mine"),

    path("<str:public_id>/accept/", AcceptProposalView.as_view(), name="proposal-accept"),
    path("<str:public_id>/reject/", RejectProposalView.as_view(), name="proposal-reject"),
    path("<str:public_id>/withdraw/", WithdrawProposalView.as_view(), name="proposal-withdraw"),
]
