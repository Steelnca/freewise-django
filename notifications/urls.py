from django.urls import path

from .views import NotificationListView, MarkReadView, UnreadCountView, NotificationStreamView

urlpatterns = [
    path("", NotificationListView.as_view(), name="notification-list"),
    path("unread-count/", UnreadCountView.as_view(), name="notification-unread-count"),
    path("stream/", NotificationStreamView.as_view(), name="notification-stream"),
    path("<str:public_id>/read/", MarkReadView.as_view(), name="notification-read"),
    path("read-all/", MarkReadView.as_view(), name="notification-read-all"),
]