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