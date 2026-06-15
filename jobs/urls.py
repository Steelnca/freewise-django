
from django.urls import path
from .views import (
    JobListView, JobCreateView, JobDetailView,
    JobUpdateView, MyJobsView, JobCategoriesView, JobDeleteView
)

urlpatterns = [
    path('',               JobListView.as_view(),    name='job-list'),
    path('create/',        JobCreateView.as_view(),  name='job-create'),
    path('mine/',          MyJobsView.as_view(),     name='job-mine'),
    path('categories/',    JobCategoriesView.as_view(), name='category-list'),
    path('<str:public_id>/',      JobDetailView.as_view(),  name='job-detail'),
    path('<str:public_id>/edit/', JobUpdateView.as_view(),  name='job-update'),
    path('<str:public_id>/delete/', JobDeleteView.as_view(),  name='job-delete'),
]