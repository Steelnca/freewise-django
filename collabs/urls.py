from django.urls import path
from .views import (
    CollabPostListView, CollabPostCreateView, CollabPostDetailView,
    ApplyCollabView, RespondCollabApplicationView,
)

urlpatterns = [
    path('',                                        CollabPostListView.as_view(),           name='collab-list'),
    path('create/',                                 CollabPostCreateView.as_view(),         name='collab-create'),
    path('<int:pk>/',                               CollabPostDetailView.as_view(),         name='collab-detail'),
    path('<int:pk>/apply/',                         ApplyCollabView.as_view(),              name='collab-apply'),
    path('applications/<int:pk>/<str:action>/',     RespondCollabApplicationView.as_view(), name='collab-application-respond'),
]