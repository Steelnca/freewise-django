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
from django.conf import settings

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from contracts.models import Contract, Milestone

from .chargily import create_checkout, verify_webhook_signature
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
from .webhooks import PaymentWebhookError, reconcile_chargily_webhook_log

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

    Creates a Chargily checkout for the milestone and stores a pending
    local wallet transaction so the webhook can settle it later.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, milestone_id):
        account = getattr(request.user, "account", None)
        client = getattr(account, "client_profile", None)
        if not client:
            return Response(
                {"detail": _("Client profile required.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            milestone = Milestone.objects.select_related(
                "contract",
                "contract__client",
                "contract__proposal",
            ).get(
                pk=milestone_id,
                contract__client=client,
            )
        except Milestone.DoesNotExist:
            return Response(
                {"detail": _("Milestone not found.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        if milestone.status != Milestone.Status.PENDING:
            return Response(
                {"detail": _("Milestone is not pending funding.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if milestone.contract.status not in {
            Contract.Status.PENDING_FUNDING,
            Contract.Status.FUNDED,
        }:
            return Response(
                {"detail": _("Contract is not ready for funding.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        wallet = get_or_create_wallet_for_user(
            request.user,
            currency=milestone.currency or DEFAULT_CURRENCY,
        )

        checkout_idempotency_key = f"chargily:checkout:milestone:{milestone.pk}"
        existing_tx = WalletTransaction.objects.filter(
            idempotency_key=checkout_idempotency_key
        ).first()

        if existing_tx and (existing_tx.metadata or {}).get("checkout_url"):
            return Response(
                {
                    "checkout_url": existing_tx.metadata["checkout_url"],
                    "checkout_id": (existing_tx.metadata or {}).get("checkout_id", ""),
                    "milestone_id": milestone.pk,
                    "amount": str(milestone.amount),
                    "currency": milestone.currency or DEFAULT_CURRENCY,
                },
                status=status.HTTP_200_OK,
            )

        frontend_base = getattr(settings, "FREEWISE_FRONTEND_URL", "http://localhost:3000").rstrip("/")
        success_url = f"{frontend_base}/dashboard/payments/success"
        failure_url = f"{frontend_base}/dashboard/payments/failure"
        webhook_url = self.request.build_absolute_uri("/api/payments/webhooks/chargily/")

        description = (
            f"Freewise — {milestone.title} "
            f"(Contract #{milestone.contract.pk})"
        )

        metadata = {
            "milestone_id": milestone.pk,
            "contract_id": milestone.contract.pk,
        }

        try:
            checkout = create_checkout(
                amount=milestone.amount,
                description=description,
                success_url=success_url,
                failure_url=failure_url,
                webhook_url=webhook_url,
                metadata=metadata,
                currency=milestone.currency or DEFAULT_CURRENCY,
            )
        except Exception:
            logger.exception(
                "Chargily checkout creation failed for milestone %s",
                milestone.pk,
            )
            return Response(
                {"detail": _("Payment gateway error.")},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        checkout_id = str(checkout.get("id", "")).strip()
        checkout_url = (
            checkout.get("checkout_url")
            or checkout.get("url")
            or checkout.get("payment_url")
            or ""
        )

        if not checkout_url:
            return Response(
                {"detail": _("Checkout URL was not returned by Chargily.")},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        pending_tx, created = WalletTransaction.objects.get_or_create(
            idempotency_key=checkout_idempotency_key,
            defaults={
                "wallet": wallet,
                "initiated_by": request.user,
                "transaction_type": WalletTransaction.Type.DEPOSIT,
                "status": WalletTransaction.Status.PENDING,
                "amount": milestone.amount,
                "currency": milestone.currency or DEFAULT_CURRENCY,
                "balance_before": wallet.available_balance,
                "balance_after": wallet.available_balance,
                "reference_type": "milestone",
                "reference_id": str(milestone.pk),
                "provider_name": "chargily",
                "provider_reference": checkout_id or checkout_idempotency_key,
                "description": _("Milestone checkout created."),
                "metadata": {
                    "checkout_id": checkout_id,
                    "checkout_url": checkout_url,
                    "milestone_id": milestone.pk,
                    "contract_id": milestone.contract.pk,
                },
            },
        )

        if not created:
            pending_tx.wallet = wallet
            pending_tx.initiated_by = request.user
            pending_tx.transaction_type = WalletTransaction.Type.DEPOSIT
            pending_tx.status = WalletTransaction.Status.PENDING
            pending_tx.amount = milestone.amount
            pending_tx.currency = milestone.currency or DEFAULT_CURRENCY
            pending_tx.balance_before = wallet.available_balance
            pending_tx.balance_after = wallet.available_balance
            pending_tx.reference_type = "milestone"
            pending_tx.reference_id = str(milestone.pk)
            pending_tx.provider_name = "chargily"
            pending_tx.provider_reference = checkout_id or checkout_idempotency_key
            pending_tx.description = _("Milestone checkout created.")
            pending_tx.metadata = {
                "checkout_id": checkout_id,
                "checkout_url": checkout_url,
                "milestone_id": milestone.pk,
                "contract_id": milestone.contract.pk,
            }
            pending_tx.full_clean()
            pending_tx.save(
                update_fields=[
                    "wallet",
                    "initiated_by",
                    "transaction_type",
                    "status",
                    "amount",
                    "currency",
                    "balance_before",
                    "balance_after",
                    "reference_type",
                    "reference_id",
                    "provider_name",
                    "provider_reference",
                    "description",
                    "metadata",
                    "updated_at",
                ]
            )

        return Response(
            {
                "checkout_url": checkout_url,
                "checkout_id": checkout_id,
                "milestone_id": milestone.pk,
                "amount": str(milestone.amount),
                "currency": milestone.currency or DEFAULT_CURRENCY,
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
        raw_body = request.body
        signature = request.headers.get("signature", "")
        signature_valid = verify_webhook_signature(raw_body, signature)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            return Response(
                {"detail": _("Invalid JSON.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        provider_event_id = str(
            payload.get("invoice_id") or payload.get("payment_id") or payload.get("id") or ""
        ).strip()
        event_name = str(payload.get("status") or payload.get("event") or "unknown").strip()

        if not provider_event_id:
            return Response(
                {"detail": _("Missing provider reference.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        webhook_log, _ = WebhookLog.objects.get_or_create(
            provider_name="chargily",
            provider_event_id=provider_event_id,
            defaults={
                "event_name": event_name,
                "status": WebhookLog.Status.RECEIVED,
                "signature_valid": signature_valid,
                "raw_body": raw_body.decode("utf-8", errors="replace"),
                "payload": payload,
                "headers": dict(request.headers),
            },
        )

        if webhook_log.processed:
            return Response(
                {"detail": _("Already processed.")},
                status=status.HTTP_200_OK,
            )

        webhook_log.signature_valid = signature_valid
        webhook_log.event_name = event_name
        webhook_log.raw_body = raw_body.decode("utf-8", errors="replace")
        webhook_log.payload = payload
        webhook_log.headers = dict(request.headers)
        webhook_log.save(
            update_fields=[
                "signature_valid",
                "event_name",
                "raw_body",
                "payload",
                "headers",
                "updated_at",
            ]
        )

        if not signature_valid:
            webhook_log.status = WebhookLog.Status.FAILED
            webhook_log.processing_error = _("Invalid webhook signature.")
            webhook_log.save(
                update_fields=["status", "processing_error", "updated_at"]
            )
            return Response(
                {"detail": _("Invalid signature.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            reconcile_chargily_webhook_log(webhook_log=webhook_log)
        except PaymentWebhookError as exc:
            webhook_log.status = WebhookLog.Status.FAILED
            webhook_log.processing_error = str(exc)
            webhook_log.save(
                update_fields=["status", "processing_error", "updated_at"]
            )
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            webhook_log.status = WebhookLog.Status.FAILED
            webhook_log.processing_error = str(exc)
            webhook_log.save(
                update_fields=["status", "processing_error", "updated_at"]
            )
            return Response(
                {"detail": _("Webhook processing failed.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"detail": _("Webhook processed successfully.")},
            status=status.HTTP_200_OK,
        )


