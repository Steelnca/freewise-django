# payments/views.py
"""
Freewise payment endpoints.

This layer is intentionally thin:
- checkout creation stays with Chargily
- balance changes stay in services
- webhook handling is idempotent
- list endpoints expose the current wallet state
"""

from __future__ import annotations
import json
import logging
from typing import Any
from urllib.parse import urlencode
from dataclasses import asdict

from django.db import transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from contracts.models import Contract, Milestone
from contracts.services import ensure_party_access

from .gateways import get_payment_gateway
from .models import Wallet, WalletTransaction, EscrowHold, Payout, WebhookLog, PaymentAttempt
from .serializers import (
    WalletSerializer,
    WalletTransactionSerializer,
    EscrowHoldSerializer,
    PayoutSerializer,
    PayoutRequestSerializer,
)
from .services import (
    get_or_create_wallet_for_user,
    request_payout,
    attach_checkout_to_payment_attempt,
    create_payment_attempt_for_milestone,
    fail_payment_attempt,
    process_payment_gateway_webhook,
    retry_payment_attempt_checkout,
    refresh_payment_attempt_from_provider,
    milestone_has_settled_or_paid_payment,
)
from .constants import DEFAULT_CURRENCY
from .reconciliation import reconcile_attempt


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

    Creates or reuses an internal payment attempt, asks the configured gateway
    for a hosted checkout, stores the checkout details, and returns the URL.
    """

    permission_classes = [IsAuthenticated]

    def _webhook_base_url(self, request) -> str:
        base_url = getattr(settings, "FREEWISE_WEBHOOK_BASE_URL", "").strip().rstrip("/")
        if base_url:
            return base_url
        return request.build_absolute_uri("/").rstrip("/")

    def _build_redirect_urls(
        self,
        request,
        *,
        attempt: PaymentAttempt,
        contract: Contract,
        milestone: Milestone,
        provider_name: str,
    ):
        frontend_base = getattr(settings, "FREEWISE_FRONTEND_URL", "http://localhost:3000").rstrip("/")

        query = urlencode(
            {
                "attempt": str(attempt.attempt_id),
                "milestone": str(milestone.id),
                "contract": str(contract.id),
                "provider": provider_name,
            }
        )

        success_url = f"{frontend_base}/payments/success?{query}"
        failure_url = f"{frontend_base}/payments/failed?{query}"
        webhook_url = f"{self._webhook_base_url(request)}/api/payments/webhooks/{provider_name}/"

        return success_url, failure_url, webhook_url

    def post(self, request, milestone_id: int):
        attempt = None

        try:
            milestone = get_object_or_404(
                Milestone.objects.select_related(
                    "contract",
                    "contract__client__account__user",
                    "contract__freelancer__account__user",
                ),
                pk=milestone_id,
            )

            contract = milestone.contract

            ensure_party_access(contract, request.user)

            account = getattr(request.user, "account", None)
            client = getattr(account, "client_profile", None)
            if not client or contract.client_id != client.id:
                raise PermissionDenied(_("Only the client can fund this milestone."))

            if contract.status not in {
                Contract.Status.PENDING_FUNDING,
                Contract.Status.IN_PROGRESS,
            }:
                raise ValidationError(
                    {"detail": _("This contract is not accepting funding right now.")}
                )

            if milestone_has_settled_or_paid_payment(milestone=milestone):
                return Response(
                    {"detail": _("This milestone is already funded. No new checkout can be created.")},
                    status=status.HTTP_409_CONFLICT,
                )

            first_pending = (
                contract.milestones.filter(status=Milestone.Status.PENDING)
                .order_by("order", "created_at")
                .first()
            )

            if not first_pending or first_pending.pk != milestone.pk:
                raise ValidationError(
                    {"detail": _("Fund the first pending milestone in order.")}
                )

            gateway = get_payment_gateway()
            provider_name = gateway.provider_name.strip().lower()

            latest_attempt = PaymentAttempt.objects.latest_for_milestone(milestone)
            reuse_existing_attempt = (
                latest_attempt
                and latest_attempt.provider == provider_name
                and not latest_attempt.is_final
            )

            if reuse_existing_attempt:
                attempt = latest_attempt
                response_status = status.HTTP_200_OK
            else:
                next_number = PaymentAttempt.objects.next_attempt_number(milestone)
                idempotency_key = (
                    request.headers.get("Idempotency-Key")
                    or request.headers.get("X-Idempotency-Key")
                    or request.data.get("idempotency_key")
                    or f"milestone:{milestone.id}:attempt:{next_number}"
                )

                attempt = create_payment_attempt_for_milestone(
                    milestone=milestone,
                    idempotency_key=idempotency_key,
                    initiated_by=request.user,
                    provider_name=provider_name,
                    success_url="",
                    failure_url="",
                    retry_of=latest_attempt if latest_attempt and latest_attempt.is_final else None,
                )
                response_status = status.HTTP_201_CREATED

            success_url, failure_url, webhook_url = self._build_redirect_urls(
                request,
                attempt=attempt,
                contract=contract,
                milestone=milestone,
                provider_name=provider_name,
            )

            if attempt.success_url != success_url or attempt.failure_url != failure_url:
                attempt.success_url = success_url
                attempt.failure_url = failure_url
                attempt.save(update_fields=["success_url", "failure_url", "updated_at"])

            if attempt.provider_checkout_id and attempt.provider_checkout_url:
                return Response(
                    {
                        "checkout_url": attempt.provider_checkout_url,
                        "checkout_id": attempt.provider_checkout_id,
                        "payment_attempt_id": str(attempt.attempt_id),
                        "milestone_id": milestone.id,
                        "amount": str(attempt.amount),
                        "currency": attempt.currency,
                        "attempt_status": attempt.internal_status,
                        "provider_status": attempt.provider_status,
                        "provider": attempt.provider,
                    },
                    status=status.HTTP_200_OK,
                )

            description = f"Freewise — {milestone.title} (Contract #{contract.pk})"

            checkout = gateway.create_checkout(
                amount=attempt.amount,
                currency=attempt.currency,
                success_url=success_url,
                failure_url=failure_url,
                webhook_url=webhook_url,
                description=description,
                metadata={
                    "attempt_id": str(attempt.attempt_id),
                    "milestone_id": milestone.id,
                    "contract_id": contract.id,
                    "provider": provider_name,
                },
                idempotency_key=attempt.idempotency_key,
            )

            attempt = attach_checkout_to_payment_attempt(
                attempt=attempt,
                provider_checkout=asdict(checkout),
                provider_status=checkout.status,
                expires_at=checkout.expires_at,
            )

            return Response(
                {
                    "checkout_url": checkout.checkout_url,
                    "checkout_id": checkout.checkout_id,
                    "payment_attempt_id": str(attempt.attempt_id),
                    "milestone_id": milestone.id,
                    "amount": str(attempt.amount),
                    "currency": attempt.currency,
                    "attempt_status": attempt.internal_status,
                    "provider_status": attempt.provider_status,
                    "provider": attempt.provider,
                },
                status=response_status,
            )

        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        except ValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise ValidationError({"detail": detail})
            return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as exc:
            if attempt is not None and not attempt.is_final:
                try:
                    fail_payment_attempt(
                        attempt=attempt,
                        reason=str(exc) or _("Failed to create payment checkout."),
                        provider_status="failed",
                    )
                except Exception:
                    logger.exception("Failed to mark payment attempt as failed")

            logger.exception("Payment checkout creation failed for milestone %s", milestone_id)
            return Response(
                {"detail": _("Failed to create payment checkout.")},
                status=status.HTTP_502_BAD_GATEWAY,
            )

@method_decorator(csrf_exempt, name="dispatch")
class PaymentGatewayWebhookView(APIView):
    """
    POST /api/payments/webhooks/<provider_name>/

    Provider-agnostic webhook endpoint.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, provider_name: str):
        try:
            gateway = get_payment_gateway(provider_name)

            raw_body = request.body or b""
            headers = {str(k): str(v) for k, v in request.headers.items()}

            attempt = process_payment_gateway_webhook(
                gateway=gateway,
                raw_body=raw_body,
                headers=headers,
            )

            return Response(
                {
                    "detail": _("Webhook processed."),
                    "payment_attempt_id": str(attempt.attempt_id),
                    "attempt_status": attempt.internal_status,
                    "provider_status": attempt.provider_status,
                },
                status=status.HTTP_200_OK,
            )

        except PermissionDenied as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )

        except ValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)

        except Exception:
            logger.exception(
                "Payment webhook processing failed for provider=%s",
                provider_name,
            )
            return Response(
                {"detail": _("Webhook processing failed.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ReconcilePaymentAttemptView(APIView):

    permission_classes = [IsAdminUser]

    def post(self, request, attempt_id):

        attempt = get_object_or_404(
            PaymentAttempt,
            attempt_id=attempt_id,
        )

        updated = reconcile_attempt(
            attempt
        )

        return Response({
            "attempt_id": str(updated.attempt_id),
            "status": updated.internal_status,
        })


class PaymentAttemptStatusView(APIView):
    """
    GET /api/payments/attempts/<uuid:attempt_id>/status/
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, attempt_id):
        attempt = get_object_or_404(
            PaymentAttempt.objects.select_related("contract", "milestone"),
            attempt_id=attempt_id,
        )

        ensure_party_access(attempt.contract, request.user)

        attempt = refresh_payment_attempt_from_provider(attempt=attempt)

        if not attempt.is_final and attempt.provider_checkout_id:
            attempt = reconcile_attempt(attempt)

        retryable = attempt.internal_status in {
            PaymentAttempt.InternalStatus.FAILED,
            PaymentAttempt.InternalStatus.CANCELED,
            PaymentAttempt.InternalStatus.EXPIRED,
        }

        return Response(
            {
                "payment_attempt_id": str(attempt.attempt_id),
                "contract_id": attempt.contract_id,
                "milestone_id": attempt.milestone_id,
                "provider": attempt.provider,
                "checkout_id": attempt.provider_checkout_id,
                "checkout_url": attempt.provider_checkout_url,
                "internal_status": attempt.internal_status,
                "provider_status": attempt.provider_status,
                "is_final": attempt.is_final,
                "retryable": retryable,
                "amount": str(attempt.amount),
                "currency": attempt.currency,
                "provider_paid_at": attempt.provider_paid_at,
                "webhook_received_at": attempt.webhook_received_at,
                "webhook_processed_at": attempt.webhook_processed_at,
                "reconciled_at": attempt.reconciled_at,
                "settled_at": attempt.settled_at,
                "failure_reason": attempt.failure_reason,
            },
            status=status.HTTP_200_OK,
        )

class RetryPaymentAttemptView(APIView):
    """
    POST /api/payments/attempts/<attempt_id>/retry/

    Reuses an open checkout if it exists.
    Creates a new checkout only when the latest attempt is retryable.
    """
    permission_classes = [IsAuthenticated]

    def _webhook_base_url(self, request) -> str:
        base_url = getattr(settings, "FREEWISE_WEBHOOK_BASE_URL", "").strip().rstrip("/")
        if base_url:
            return base_url
        return request.build_absolute_uri("/").rstrip("/")

    def _build_redirect_urls(self, request, *, attempt: PaymentAttempt):
        frontend_base = getattr(settings, "FREEWISE_FRONTEND_URL", "http://localhost:3000").rstrip("/")

        query = urlencode(
            {
                "attempt": str(attempt.attempt_id),
                "milestone": str(attempt.milestone_id),
                "contract": str(attempt.contract_id),
                "provider": attempt.provider,
            }
        )

        success_url = f"{frontend_base}/payments/success?{query}"
        failure_url = f"{frontend_base}/payments/failed?{query}"
        webhook_url = f"{self._webhook_base_url(request)}/api/payments/webhooks/{attempt.provider}/"
        return success_url, failure_url, webhook_url

    def post(self, request, attempt_id):
        try:
            attempt = get_object_or_404(
                PaymentAttempt.objects.select_related("contract", "milestone"),
                attempt_id=attempt_id,
            )

            ensure_party_access(attempt.contract, request.user)

            account = getattr(request.user, "account", None)
            client = getattr(account, "client_profile", None)
            if not client or attempt.contract.client_id != client.id:
                raise PermissionDenied(_("Only the client can retry this payment."))

            if milestone_has_settled_or_paid_payment(milestone=attempt.milestone):
                return Response(
                    {"detail": _("This milestone is already funded. Retry is disabled.")},
                    status=status.HTTP_409_CONFLICT,
                )

            gateway = get_payment_gateway(attempt.provider)

            success_url, failure_url, webhook_url = self._build_redirect_urls(
                request,
                attempt=attempt,
            )

            next_key = (
                request.headers.get("Idempotency-Key")
                or request.headers.get("X-Idempotency-Key")
                or f"retry:milestone:{attempt.milestone_id}:attempt:{attempt.attempt_number + 1}"
            )

            retry_attempt = retry_payment_attempt_checkout(
                attempt=attempt,
                gateway=gateway,
                idempotency_key=next_key,
                initiated_by=request.user,
                success_url=success_url,
                failure_url=failure_url,
                webhook_url=webhook_url,
            )

            return Response(
                {
                    "checkout_url": retry_attempt.provider_checkout_url,
                    "checkout_id": retry_attempt.provider_checkout_id,
                    "payment_attempt_id": str(retry_attempt.attempt_id),
                    "milestone_id": retry_attempt.milestone_id,
                    "amount": str(retry_attempt.amount),
                    "currency": retry_attempt.currency,
                    "attempt_status": retry_attempt.internal_status,
                    "provider_status": retry_attempt.provider_status,
                    "provider": retry_attempt.provider,
                },
                status=status.HTTP_200_OK if retry_attempt.pk == attempt.pk else status.HTTP_201_CREATED,
            )

        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        except ValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            text = str(detail).lower()
            code = status.HTTP_409_CONFLICT if "already paid" in text else status.HTTP_400_BAD_REQUEST
            return Response({"detail": detail}, status=code)

        except Exception:
            logger.exception("Retry checkout failed for attempt=%s", attempt_id)
            return Response(
                {"detail": _("Failed to create retry checkout.")},
                status=status.HTTP_502_BAD_GATEWAY,
            )