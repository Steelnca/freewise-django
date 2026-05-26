# payments/views.py
"""
Freewise payment endpoints.

This layer is intentionally thin:
- checkout creation stays with Chargily
- balance changes stay in services
- webhook handling is idempotent
- list endpoints expose the current wallet state
"""

import json
import logging

from django.db import transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.utils.translation import gettext_lazy as _

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from contracts.models import Milestone

from . import chargily
from .models import Wallet, WalletTransaction, EscrowHold, Payout, WebhookLog
from .serializers import (
    WalletSerializer,
    WalletTransactionSerializer,
    EscrowHoldSerializer,
    PayoutSerializer,
    PayoutRequestSerializer,
)
from .services import (
    get_or_create_wallet_for_user,
    record_deposit,
    hold_funds_for_escrow,
    request_payout,
)
from .constants import DEFAULT_CURRENCY
from .webhooks import process_successful_payment

logger = logging.getLogger(__name__)


def get_user_account(user):
    return getattr(user, "account", None)


class WalletView(APIView):
    """
    GET /api/payments/wallet/
    Returns the current user's wallet summary.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet = get_or_create_wallet_for_user(
            request.user,
            currency=DEFAULT_CURRENCY,
        )

        return Response(
            WalletSerializer(wallet).data,
            status=status.HTTP_200_OK,
        )

class WalletTransactionsView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = WalletTransactionSerializer

    def get_queryset(self):
        wallet = get_or_create_wallet_for_user(
            self.request.user,
            currency=DEFAULT_CURRENCY,
        )
        return (
            WalletTransaction.objects.filter(wallet=wallet)
            .select_related("wallet", "initiated_by")
            .order_by("-created_at")
        )

class MyEscrowView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EscrowHoldSerializer

    def get_queryset(self):
        wallet = get_or_create_wallet_for_user(
            self.request.user,
            currency=DEFAULT_CURRENCY,
        )
        return (
            EscrowHold.objects.filter(wallet=wallet)
            .select_related("wallet", "funding_transaction", "resolution_transaction")
            .order_by("-created_at")
        )


class MyPayoutsView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PayoutSerializer

    def get_queryset(self):
        wallet = get_or_create_wallet_for_user(
            self.request.user,
            currency=DEFAULT_CURRENCY,
        )
        return (
            Payout.objects.filter(wallet=wallet)
            .select_related("wallet", "ledger_transaction")
            .order_by("-created_at")
        )


class RequestPayoutView(APIView):
    """
    POST /api/payments/payouts/request/
    Creates a payout request from the current user's wallet.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        account = get_user_account(request.user)
        if not account:
            return Response(
                {"detail": _("Account not found.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        freelancer = getattr(account, "freelancer_profile", None)
        if not freelancer:
            return Response(
                {"detail": _("Freelancer profile required.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        wallet = Wallet.objects.filter(user=request.user).first()
        if not wallet:
            return Response(
                {"detail": _("Wallet not found.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PayoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payout = request_payout(
            wallet=wallet,
            amount=serializer.validated_data["amount"],
            idempotency_key=serializer.validated_data["idempotency_key"],
            initiated_by=request.user,
            provider_name=serializer.validated_data.get("provider_name", ""),
            provider_reference=serializer.validated_data.get("provider_reference", ""),
            destination_type=serializer.validated_data.get("destination_type", ""),
            destination_label=serializer.validated_data.get("destination_label", ""),
            description=serializer.validated_data.get("description", ""),
            metadata=serializer.validated_data.get("metadata", {}),
        )

        return Response(
            PayoutSerializer(payout).data,
            status=status.HTTP_201_CREATED,
        )


class FundMilestoneView(APIView):
    """
    POST /api/payments/fund/<milestone_id>/
    Creates the Chargily checkout for a milestone.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, milestone_id):
        account = get_user_account(request.user)
        client = getattr(account, "client_profile", None)
        if not client:
            return Response(
                {"detail": _("Client profile required.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            milestone = Milestone.objects.select_related("contract__client").get(
                pk=milestone_id,
                contract__client=client,
                status=Milestone.Status.PENDING,
            )
        except Milestone.DoesNotExist:
            return Response(
                {"detail": _("Milestone not found or already funded.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        base_url = request.build_absolute_uri("/").rstrip("/")
        success_url = f"{base_url}/dashboard/payments/success"
        failure_url = f"{base_url}/dashboard/payments/failure"
        webhook_url = request.build_absolute_uri("/api/payments/webhook/")

        try:
            checkout = chargily.create_checkout(
                milestone,
                success_url,
                failure_url,
                webhook_url,
            )
        except Exception as exc:
            logger.exception("Chargily checkout creation failed for milestone %s", milestone.pk)
            return Response(
                {"detail": _("Payment gateway error.")},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {
                "checkout_url": checkout.get("checkout_url"),
                "checkout_id": checkout.get("id", ""),
                "milestone_id": milestone.pk,
                "amount": str(milestone.amount),
                "currency": DEFAULT_CURRENCY,
            },
            status=status.HTTP_200_OK,
        )


class ChargilyWebhookView(APIView):
    """
    POST /api/payments/webhooks/chargily/

    Handles Chargily payment webhooks.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        payload = request.data

        provider_reference = payload.get("invoice_id")
        status_value = payload.get("status")

        if not provider_reference:
            return Response(
                {"detail": _("Missing provider reference.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment = WalletTransaction.objects.filter(
            provider_reference=provider_reference
        ).first()

        if not payment:
            return Response(
                {"detail": _("Payment not found.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        webhook_log = WebhookLog.objects.create(
            provider_name="chargily",
            event_type=status_value or "unknown",
            payload=payload,
            provider_reference=provider_reference,
        )

        try:
            if status_value == "paid":
                process_successful_payment(payment=payment)

            webhook_log.processed = True
            webhook_log.save(update_fields=["processed", "updated_at"])

        except Exception as exc:
            webhook_log.processing_error = str(exc)
            webhook_log.save(
                update_fields=[
                    "processing_error",
                    "updated_at",
                ]
            )

            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"detail": _("Webhook processed successfully.")},
            status=status.HTTP_200_OK,
        )


