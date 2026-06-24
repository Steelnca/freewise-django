from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from django.db import transaction

from billing.services import assign_default_subscription_for_role
from billing.models import SubscriptionPlan

from .models import Account
from .serializers import AccountSerializer, AccountUpdateSerializer


class AccountMeView(APIView):
    """
    GET  /api/accounts/me/  → current user's full account
    PUT  /api/accounts/me/  → update account fields
    """
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        account = getattr(request.user, 'account', None)
        if not account:
            return Response({'detail': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(AccountSerializer(account, context={'request': request}).data)

    def put(self, request):
        account = getattr(request.user, 'account', None)
        if not account:
            return Response({'detail': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = AccountUpdateSerializer(account, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(AccountSerializer(account, context={'request': request}).data)


class ActivateRoleView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        role = str(request.data.get("role") or "").strip().lower()

        if role not in {"freelancer", "client"}:
            return Response({"detail": "Invalid role."}, status=status.HTTP_400_BAD_REQUEST)

        account = getattr(request.user, "account", None)
        if not account:
            return Response({"detail": "Account not found."}, status=status.HTTP_400_BAD_REQUEST)

        if role == "freelancer":
            account.is_freelancer = True
            account.save(update_fields=["is_freelancer"])
            assign_default_subscription_for_role(request.user, SubscriptionPlan.Role.FREELANCER)

        elif role == "client":
            account.is_client = True
            account.save(update_fields=["is_client"])
            assign_default_subscription_for_role(request.user, SubscriptionPlan.Role.CLIENT)

        return Response(
            {"detail": "Role activated successfully."},
            status=status.HTTP_200_OK,
        )