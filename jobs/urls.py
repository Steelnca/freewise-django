
from django.urls import path
from .views import (
    JobListView, JobCreateView, JobDetailView,
    JobUpdateView, MyJobsView, CategoryListView,
)

urlpatterns = [
    path('',               JobListView.as_view(),    name='job-list'),
    path('create/',        JobCreateView.as_view(),  name='job-create'),
    path('mine/',          MyJobsView.as_view(),     name='job-mine'),
    path('categories/',    CategoryListView.as_view(), name='category-list'),
    path('<int:pk>/',      JobDetailView.as_view(),  name='job-detail'),
    path('<int:pk>/edit/', JobUpdateView.as_view(),  name='job-update'),
]