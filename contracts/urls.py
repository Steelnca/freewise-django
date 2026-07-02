from django.urls import path

from .views import (
    ContractListView,
    ContractDetailView,
    ContractDetailView,
    ContractListView,
    ContractCancelView,
    ContractEventsView,
)

app_name = "contracts"


urlpatterns = [
    path("", ContractListView.as_view(), name="contract-list"),

    path("<str:public_id>/events/", ContractEventsView.as_view(), name="contract-events"),
    path("<str:public_id>/cancel/", ContractCancelView.as_view(), name="contract-cancel"),
    path("<str:public_id>/", ContractDetailView.as_view(), name="contract-detail"),
]