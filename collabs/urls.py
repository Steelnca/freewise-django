from django.urls import path

from .views import CollabAcceptView, CollabApplyView, CollabRequestCreateView

urlpatterns = [

    path(
        "milestones/<str:milestone_public_id>/collabs/",
        CollabRequestCreateView.as_view(),
        name="collab-request-create",
    ),
    path(
        "collabs/<str:request_public_id>/apply/",
        CollabApplyView.as_view(),
        name="collab-apply",
    ),
    path(
        "collab-applications/<str:application_public_id>/accept/",
        CollabAcceptView.as_view(),
        name="collab-accept",
    ),
]