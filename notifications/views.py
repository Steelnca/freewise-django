from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import Notification
from .serializers import NotificationSerializer


class NotificationListView(generics.ListAPIView):
    """
    GET /api/notifications/  → current user's notifications (unread first)
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = NotificationSerializer

    def get_queryset(self):
        account = getattr(self.request.user, 'account', None)
        if not account:
            return Notification.objects.none()
        return Notification.objects.filter(account=account).order_by('is_read', '-created_at')


class MarkReadView(APIView):
    """
    POST /api/notifications/<id>/read/  → mark single notification as read
    POST /api/notifications/read-all/   → mark all as read
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        account = getattr(request.user, 'account', None)
        if not account:
            return Response({'detail': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)

        if pk:
            try:
                notif = Notification.objects.get(pk=pk, account=account)
                notif.is_read = True
                notif.save(update_fields=['is_read'])
            except Notification.DoesNotExist:
                return Response({'detail': 'Notification not found.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            Notification.objects.filter(account=account, is_read=False).update(is_read=True)

        return Response({'detail': 'Marked as read.'})


class UnreadCountView(APIView):
    """
    GET /api/notifications/unread-count/  → number of unread notifications
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account = getattr(request.user, 'account', None)
        if not account:
            return Response({'count': 0})
        count = Notification.objects.filter(account=account, is_read=False).count()
        return Response({'count': count})

import json
from queue import Empty as QueueEmpty

from django.http import StreamingHttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Notification
from .pubsub import NotificationHub, serialize_notification


def _sse(event: str, payload: dict, event_id: int | None = None) -> str:
    """
    Format one SSE message.
    """
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


class NotificationStreamView(APIView):
    """
    GET /api/notifications/stream/

    Live push stream for new notifications.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account = getattr(request.user, "account", None)
        if not account:
            return Response(
                {"detail": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        subscriber_queue = NotificationHub.subscribe(account.id)

        def stream():
            try:
                # Initial unread snapshot so the client can render immediately.
                unread = (
                    Notification.objects.filter(account=account, is_read=False)
                    .order_by("created_at")
                    .only("id", "type", "title", "message", "link", "is_read", "created_at")
                )
                for notification in unread:
                    yield _sse(
                        "notification",
                        serialize_notification(notification),
                        notification.id,
                    )

                # Live updates.
                while True:
                    try:
                        payload = subscriber_queue.get(timeout=15)
                        yield _sse("notification", payload, payload.get("id"))
                    except QueueEmpty:
                        # Keep the connection alive.
                        yield ": keep-alive\n\n"
            finally:
                NotificationHub.unsubscribe(account.id, subscriber_queue)

        response = StreamingHttpResponse(
            stream(),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response