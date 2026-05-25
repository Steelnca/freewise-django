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
    DEFAULT_CURRENCY,
    get_or_create_wallet_for_user,
    record_deposit,
    hold_funds_for_escrow,
    request_payout,
)

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
        wallet = Wallet.objects.filter(user=request.user).first()
        if not wallet:
            return Response(
                {"detail": _("Wallet not found.")},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(WalletSerializer(wallet).data)


class MyEscrowView(generics.ListAPIView):
    """
    GET /api/payments/escrow/
    Returns escrow holds for the current user's wallet.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = EscrowHoldSerializer

    def get_queryset(self):
        wallet = Wallet.objects.filter(user=self.request.user).first()
        if not wallet:
            return EscrowHold.objects.none()
        return (
            EscrowHold.objects.filter(wallet=wallet)
            .select_related("wallet", "funding_transaction", "resolution_transaction")
            .order_by("-created_at")
        )


class MyPayoutsView(generics.ListAPIView):
    """
    GET /api/payments/payouts/
    Returns payout history for the current user's wallet.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = PayoutSerializer

    def get_queryset(self):
        wallet = Wallet.objects.filter(user=self.request.user).first()
        if not wallet:
            return Payout.objects.none()
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


@method_decorator(csrf_exempt, name="dispatch")
class ChargilyWebhookView(APIView):
    """
    POST /api/payments/webhook/
    Receives Chargily events and reconciles them into the ledger.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        payload = request.body
        signature = request.headers.get("signature", "")

        if not chargily.verify_webhook_signature(payload, signature):
            return Response(
                {"detail": _("Invalid signature.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return Response(
                {"detail": _("Invalid JSON.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event_id = data.get("id", "")
        event_type = data.get("type", "")

        log, created = WebhookLog.objects.get_or_create(
            provider_name="chargily",
            provider_event_id=event_id,
            defaults={
                "event_name": event_type,
                "raw_body": payload.decode("utf-8", errors="replace"),
                "payload": data,
                "headers": dict(request.headers),
            },
        )

        if log.processed:
            return Response({"detail": _("Already processed.")}, status=status.HTTP_200_OK)

        try:
            self._handle_event(event_type, data, event_id)
            log.status = WebhookLog.Status.PROCESSED
            log.processed = True
            log.processed_at = timezone.now()
            log.save(update_fields=["status", "processed", "processed_at", "updated_at"])
        except Exception as exc:
            logger.exception("Chargily webhook processing failed.")
            log.status = WebhookLog.Status.FAILED
            log.processing_error = str(exc)
            log.save(update_fields=["status", "processing_error", "updated_at"])
            return Response(
                {"detail": _("Processing error.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({"detail": _("OK")}, status=status.HTTP_200_OK)

    def _handle_event(self, event_type: str, data: dict, event_id: str):
        payload_data = data.get("data") or {}
        metadata = payload_data.get("metadata") or data.get("metadata") or {}

        if event_type != "checkout.paid":
            return

        milestone_id = metadata.get("milestone_id")
        if not milestone_id:
            raise ValueError("Missing milestone_id in Chargily metadata.")

        milestone = Milestone.objects.select_related(
            "contract__client__account__user"
        ).get(pk=milestone_id)

        # Idempotency is handled in the service layer; repeated webhooks are safe.
        wallet = get_or_create_wallet_for_user(
            milestone.contract.client.account.user,
            currency=DEFAULT_CURRENCY,
        )

        record_deposit(
            wallet=wallet,
            amount=milestone.amount,
            idempotency_key=f"chargily:{event_id}:deposit",
            provider_name="chargily",
            provider_reference=payload_data.get("payment_id", ""),
            reference_type="milestone",
            reference_id=str(milestone.pk),
            description=_("Client payment received."),
            metadata={
                "milestone_id": milestone.pk,
                "contract_id": milestone.contract_id,
            },
        )

        hold_funds_for_escrow(
            wallet=wallet,
            amount=milestone.amount,
            contract_reference=f"contract:{milestone.contract_id}",
            idempotency_key=f"chargily:{event_id}:escrow",
            reference_type="milestone",
            reference_id=str(milestone.pk),
            description=_("Funds moved into escrow."),
            metadata={
                "milestone_id": milestone.pk,
                "contract_id": milestone.contract_id,
            },
        )

        if milestone.status != Milestone.Status.FUNDED:
            milestone.status = Milestone.Status.FUNDED
            milestone.paid_at = timezone.now() if hasattr(milestone, "paid_at") else None
            milestone.save(update_fields=["status", "updated_at"])


class WalletTransactionsView(generics.ListAPIView):
    """
    GET /api/payments/transactions/
    Returns the current user's wallet transaction history.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = WalletTransactionSerializer

    def get_queryset(self):
        wallet = Wallet.objects.filter(user=self.request.user).first()
        if not wallet:
            return WalletTransaction.objects.none()
        return (
            WalletTransaction.objects.filter(wallet=wallet)
            .select_related("wallet", "initiated_by")
            .order_by("-created_at")
        )