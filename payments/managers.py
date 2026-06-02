
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional
from django.utils import timezone
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone


class PaymentAttemptQuerySet(models.QuerySet):
    def for_milestone(self, milestone):
        return self.filter(milestone=milestone)

    def for_contract(self, contract):
        return self.filter(contract=contract)

    def open(self):
        return self.filter(
            internal_status__in=[
                self.model.InternalStatus.CREATED,
                self.model.InternalStatus.REDIRECTED,
                self.model.InternalStatus.PENDING_PROVIDER,
                self.model.InternalStatus.PROCESSING,
                self.model.InternalStatus.PAID_PROVIDER_NOT_SETTLED,
                self.model.InternalStatus.RECONCILED,
            ]
        )

    def final(self):
        return self.filter(
            internal_status__in=[
                self.model.InternalStatus.SETTLED,
                self.model.InternalStatus.FAILED,
                self.model.InternalStatus.CANCELED,
                self.model.InternalStatus.EXPIRED,
            ]
        )

    def retryable(self):
        return self.filter(
            internal_status__in=[
                self.model.InternalStatus.FAILED,
                self.model.InternalStatus.CANCELED,
                self.model.InternalStatus.EXPIRED,
            ]
        )

    def unresolved(self):
        return self.open().exclude(
            internal_status=self.model.InternalStatus.PAID_PROVIDER_NOT_SETTLED
        )

    def stale(self, minutes: int = 5):
        cutoff = timezone.now() - timedelta(minutes=minutes)
        return self.open().filter(updated_at__lte=cutoff)

    def latest_for_milestone(self, milestone):
        return (
            self.for_milestone(milestone)
            .order_by("-attempt_number", "-created_at")
            .first()
        )


class PaymentAttemptManager(models.Manager.from_queryset(PaymentAttemptQuerySet)):
    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("contract", "milestone", "initiated_by", "retry_of")
        )

    def for_milestone(self, milestone):
        return self.get_queryset().for_milestone(milestone)

    def for_contract(self, contract):
        return self.get_queryset().for_contract(contract)

    def latest_for_milestone(self, milestone):
        return self.get_queryset().latest_for_milestone(milestone)

    @transaction.atomic
    def next_attempt_number(self, milestone) -> int:
        """
        Safe even under concurrency: lock the milestone's rows before computing.
        """
        locked_attempts = list(
            self.select_for_update()
            .filter(milestone=milestone)
            .values_list("attempt_number", flat=True)
        )
        return (max(locked_attempts) if locked_attempts else 0) + 1

    @transaction.atomic
    def create_attempt(
        self,
        *,
        milestone,
        idempotency_key: str,
        initiated_by=None,
        provider: str = "",
        retry_of=None,
        success_url: str = "",
        failure_url: str = "",
        provider_snapshot: Optional[dict[str, Any]] = None,
        provider_status: str = "",
    ):
        """
        Create or return the same attempt safely.

        - idempotency_key prevents duplicates
        - attempt_number is monotonic per milestone
        - retry_of links chained attempts
        """
        if not idempotency_key:
            raise ValidationError({"idempotency_key": "Idempotency key is required."})

        existing = self.select_for_update().filter(idempotency_key=idempotency_key).first()
        if existing:
            if existing.milestone_id != milestone.id:
                raise ValidationError(
                    {"idempotency_key": "This idempotency key is already used for another milestone."}
                )
            return existing

        contract = milestone.contract
        currency = milestone.currency or contract.currency

        attempt = self.model(
            contract=contract,
            milestone=milestone,
            initiated_by=initiated_by,
            provider=provider or self.model.Provider.CHARGILY,
            attempt_number=self.next_attempt_number(milestone),
            amount=milestone.amount,
            currency=currency,
            internal_status=self.model.InternalStatus.CREATED,
            provider_status=provider_status or "",
            idempotency_key=idempotency_key,
            success_url=success_url or "",
            failure_url=failure_url or "",
            retry_of=retry_of,
            provider_snapshot=provider_snapshot or {},
        )
        attempt.full_clean()
        attempt.save()
        return attempt

    @transaction.atomic
    def attach_provider_checkout(
        self,
        attempt,
        *,
        provider_checkout_id: str,
        provider_checkout_url: str,
        provider_status: str = "",
        expires_at=None,
        provider_snapshot: Optional[dict[str, Any]] = None,
    ):
        """
        Store the provider checkout details after Freewise creates the checkout.
        """
        attempt = self.select_for_update().get(pk=attempt.pk)

        attempt.provider_checkout_id = provider_checkout_id or attempt.provider_checkout_id
        attempt.provider_checkout_url = provider_checkout_url or attempt.provider_checkout_url
        attempt.provider_status = provider_status or attempt.provider_status
        attempt.expires_at = expires_at or attempt.expires_at
        attempt.provider_snapshot = provider_snapshot or attempt.provider_snapshot
        attempt.provider_created_at = attempt.provider_created_at or timezone.now()
        attempt.internal_status = self.model.InternalStatus.REDIRECTED

        attempt.save(
            update_fields=[
                "provider_checkout_id",
                "provider_checkout_url",
                "provider_status",
                "expires_at",
                "provider_snapshot",
                "provider_created_at",
                "internal_status",
                "updated_at",
            ]
        )
        return attempt

    @transaction.atomic
    def record_webhook(
        self,
        attempt,
        *,
        payload: Optional[dict[str, Any]] = None,
        provider_status: str = "",
        provider_snapshot: Optional[dict[str, Any]] = None,
    ):
        """
        Save webhook payload once, safely.
        """
        attempt = self.select_for_update().get(pk=attempt.pk)

        attempt.webhook_payload = payload or attempt.webhook_payload
        attempt.webhook_received_at = attempt.webhook_received_at or timezone.now()
        if provider_status:
            attempt.provider_status = provider_status
        if provider_snapshot:
            attempt.provider_snapshot = provider_snapshot

        attempt.save(
            update_fields=[
                "webhook_payload",
                "webhook_received_at",
                "provider_status",
                "provider_snapshot",
                "updated_at",
            ]
        )
        return attempt

    @transaction.atomic
    def reconcile_from_provider(
        self,
        attempt,
        *,
        provider_status: str,
        provider_snapshot: Optional[dict[str, Any]] = None,
    ):
        """
        Update the attempt from the provider's current checkout status.
        """
        attempt = self.select_for_update().get(pk=attempt.pk)

        attempt.provider_status = provider_status or attempt.provider_status
        if provider_snapshot:
            attempt.provider_snapshot = provider_snapshot

        status_map = {
            "pending": self.model.InternalStatus.PENDING_PROVIDER,
            "processing": self.model.InternalStatus.PROCESSING,
            "paid": self.model.InternalStatus.PAID_PROVIDER_NOT_SETTLED,
            "failed": self.model.InternalStatus.FAILED,
            "canceled": self.model.InternalStatus.CANCELED,
            "expired": self.model.InternalStatus.EXPIRED,
        }
        attempt.internal_status = status_map.get(
            (provider_status or "").strip().lower(),
            attempt.internal_status,
        )
        attempt.reconciled_at = timezone.now()

        attempt.save(
            update_fields=[
                "provider_status",
                "provider_snapshot",
                "internal_status",
                "reconciled_at",
                "updated_at",
            ]
        )
        return attempt

    @transaction.atomic
    def mark_settled(
        self,
        attempt,
        *,
        settlement_transaction=None,
        escrow_hold=None,
        provider_status: str = "paid",
    ):
        """
        Mark the attempt fully settled in Freewise.
        """
        attempt = self.select_for_update().get(pk=attempt.pk)
        attempt.internal_status = self.model.InternalStatus.SETTLED
        attempt.provider_status = provider_status or attempt.provider_status
        attempt.settled_at = attempt.settled_at or timezone.now()

        if settlement_transaction is not None:
            attempt.settlement_transaction = settlement_transaction
        if escrow_hold is not None:
            attempt.escrow_hold = escrow_hold

        attempt.save(
            update_fields=[
                "internal_status",
                "provider_status",
                "settled_at",
                "settlement_transaction",
                "escrow_hold",
                "updated_at",
            ]
        )
        return attempt

    @transaction.atomic
    def mark_failed(
        self,
        attempt,
        *,
        reason: str = "",
        provider_status: str = "failed",
    ):
        attempt = self.select_for_update().get(pk=attempt.pk)
        attempt.internal_status = self.model.InternalStatus.FAILED
        attempt.provider_status = provider_status or attempt.provider_status
        if reason:
            attempt.failure_reason = reason

        attempt.save(
            update_fields=[
                "internal_status",
                "provider_status",
                "failure_reason",
                "updated_at",
            ]
        )
        return attempt

    @transaction.atomic
    def mark_canceled(
        self,
        attempt,
        *,
        reason: str = "",
        provider_status: str = "canceled",
    ):
        attempt = self.select_for_update().get(pk=attempt.pk)
        attempt.internal_status = self.model.InternalStatus.CANCELED
        attempt.provider_status = provider_status or attempt.provider_status
        if reason:
            attempt.failure_reason = reason

        attempt.save(
            update_fields=[
                "internal_status",
                "provider_status",
                "failure_reason",
                "updated_at",
            ]
        )
        return attempt

    @transaction.atomic
    def mark_expired(
        self,
        attempt,
        *,
        reason: str = "",
        provider_status: str = "expired",
    ):
        attempt = self.select_for_update().get(pk=attempt.pk)
        attempt.internal_status = self.model.InternalStatus.EXPIRED
        attempt.provider_status = provider_status or attempt.provider_status
        if reason:
            attempt.failure_reason = reason

        attempt.save(
            update_fields=[
                "internal_status",
                "provider_status",
                "failure_reason",
                "updated_at",
            ]
        )
        return attempt

    @transaction.atomic
    def create_retry(
        self,
        previous_attempt,
        *,
        idempotency_key: str,
        initiated_by=None,
        success_url: str = "",
        failure_url: str = "",
    ):
        """
        Create a new attempt chained from a previous one.
        """
        return self.create_attempt(
            milestone=previous_attempt.milestone,
            idempotency_key=idempotency_key,
            initiated_by=initiated_by,
            provider=previous_attempt.provider,
            retry_of=previous_attempt,
            success_url=success_url or previous_attempt.success_url,
            failure_url=failure_url or previous_attempt.failure_url,
        )