from django.urls import path
from .views import (
    FreelancerListView,
    FreelancerProfileMeView,
    FreelancerProfileDetailView,
    SkillListView,
)

urlpatterns = [
    path('',             FreelancerListView.as_view(),          name='freelancer-list'),
    path('me/',          FreelancerProfileMeView.as_view(),     name='freelancer-me'),
    path('skills/',      SkillListView.as_view(),               name='skill-list'),
    path('<slug:slug>/', FreelancerProfileDetailView.as_view(), name='freelancer-detail'),
]