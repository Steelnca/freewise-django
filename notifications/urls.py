
from django.urls import path
from .views import NotificationListView, MarkReadView, UnreadCountView

urlpatterns = [
    path('',                    NotificationListView.as_view(), name='notification-list'),
    path('unread-count/',       UnreadCountView.as_view(),      name='notification-unread-count'),
    path('read-all/',           MarkReadView.as_view(),         name='notification-read-all'),
    path('<int:pk>/read/',      MarkReadView.as_view(),         name='notification-read'),
]