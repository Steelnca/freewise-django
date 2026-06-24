
from django.urls import path
from .views import (
    JobListView, JobCreateView, JobDetailView,
    JobUpdateView, MyJobsView, JobCategoriesView, JobPublishView, JobPauseView, JobCloseView, JobArchiveView,
    JobApplicantsView, JobApplicantWorkspaceView, JobProposalSubmitView
)

urlpatterns = [
    path('',               JobListView.as_view(),    name='job-list'),
    path('create/',        JobCreateView.as_view(),  name='job-create'),
    path('mine/',          MyJobsView.as_view(),     name='job-mine'),

    path('categories/',    JobCategoriesView.as_view(), name='category-list'),
    path('<str:public_id>/',      JobDetailView.as_view(),  name='job-detail'),
    path('<str:public_id>/edit/', JobUpdateView.as_view(),  name='job-update'),
    path("<str:public_id>/publish/",JobPublishView.as_view(), name='job-publish'),
    path("<str:public_id>/pause/", JobPauseView.as_view(), name='job-pause'),
    path("<str:public_id>/close/", JobCloseView.as_view(), name='job-close'),
    path("<str:public_id>/archive/", JobArchiveView.as_view(), name='job-archive'),

    path("<str:public_id>/submit/", JobProposalSubmitView.as_view(), name="proposal-submit"),

    path("<str:public_id>/applicants/", JobApplicantsView.as_view(), name="job-applicants"),
    path("<str:public_id>/applicants/<str:proposal_public_id>/",
        JobApplicantWorkspaceView.as_view(),
        name="applicant-detail",
    ),

]