from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from core.utils import json_safe_dict


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
        return self.open()

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
        # Keep this safe: only non-nullable relations here.
        return super().get_queryset().select_related("contract", "milestone")

    def for_milestone(self, milestone):
        return self.get_queryset().for_milestone(milestone)

    def for_contract(self, contract):
        return self.get_queryset().for_contract(contract)

    def open(self):
        return self.get_queryset().open()

    def final(self):
        return self.get_queryset().final()

    def retryable(self):
        return self.get_queryset().retryable()

    def unresolved(self):
        return self.get_queryset().unresolved()

    def stale(self, minutes: int = 5):
        return self.get_queryset().stale(minutes=minutes)

    def latest_for_milestone(self, milestone):
        return self.get_queryset().latest_for_milestone(milestone)

    @transaction.atomic
    def next_attempt_number(self, milestone) -> int:
        """
        Safe under concurrency: lock the rows for this milestone before numbering.
        """
        existing_numbers = list(
            self.model._base_manager.select_for_update()
            .filter(milestone=milestone)
            .values_list("attempt_number", flat=True)
        )
        return (max(existing_numbers) if existing_numbers else 0) + 1

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

        if retry_of is not None and retry_of.milestone_id != milestone.id:
            raise ValidationError(
                {"retry_of": "Retry attempts must belong to the same milestone."}
            )

        existing = self.model._base_manager.filter(
            idempotency_key=idempotency_key
        ).first()
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
            provider_snapshot=json_safe_dict(provider_snapshot),
        )
        attempt.full_clean()

        try:
            attempt.save()
        except IntegrityError:
            return self.model._base_manager.get(idempotency_key=idempotency_key)

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
        Store provider checkout details after checkout creation.
        """
        attempt = (
            self.model._base_manager.select_for_update()
            .select_related("contract", "milestone")
            .get(pk=attempt.pk)
        )

        attempt.provider_checkout_id = provider_checkout_id or attempt.provider_checkout_id
        attempt.provider_checkout_url = provider_checkout_url or attempt.provider_checkout_url
        attempt.provider_status = provider_status or attempt.provider_status
        attempt.expires_at = expires_at or attempt.expires_at
        attempt.provider_snapshot = json_safe_dict(provider_snapshot or attempt.provider_snapshot)
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
        attempt = (
            self.model._base_manager.select_for_update()
            .select_related("contract", "milestone")
            .get(pk=attempt.pk)
        )

        attempt.webhook_payload = json_safe_dict(payload or attempt.webhook_payload)
        attempt.webhook_received_at = attempt.webhook_received_at or timezone.now()
        if provider_status:
            attempt.provider_status = provider_status
        if provider_snapshot is not None:
            attempt.provider_snapshot = json_safe_dict(provider_snapshot)

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
        attempt = (
            self.model._base_manager.select_for_update()
            .select_related("contract", "milestone")
            .get(pk=attempt.pk)
        )

        attempt.provider_status = provider_status or attempt.provider_status
        if provider_snapshot is not None:
            attempt.provider_snapshot = json_safe_dict(provider_snapshot)

        status_map = {
            "pending": self.model.InternalStatus.PENDING_PROVIDER,
            "processing": self.model.InternalStatus.PROCESSING,
            "paid": self.model.InternalStatus.PAID_PROVIDER_NOT_SETTLED,
            "failed": self.model.InternalStatus.FAILED,
            "canceled": self.model.InternalStatus.CANCELED,
            "expired": self.model.InternalStatus.EXPIRED,
        }

        normalized = (provider_status or "").strip().lower()
        attempt.internal_status = status_map.get(
            normalized,
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
        attempt = (
            self.model._base_manager.select_for_update()
            .select_related("contract", "milestone")
            .get(pk=attempt.pk)
        )

        if attempt.internal_status == self.model.InternalStatus.SETTLED:
            return attempt

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
        attempt = (
            self.model._base_manager.select_for_update()
            .select_related("contract", "milestone")
            .get(pk=attempt.pk)
        )

        if attempt.internal_status == self.model.InternalStatus.FAILED:
            return attempt

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
        attempt = (
            self.model._base_manager.select_for_update()
            .select_related("contract", "milestone")
            .get(pk=attempt.pk)
        )

        if attempt.internal_status == self.model.InternalStatus.CANCELED:
            return attempt

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
        attempt = (
            self.model._base_manager.select_for_update()
            .select_related("contract", "milestone")
            .get(pk=attempt.pk)
        )

        if attempt.internal_status == self.model.InternalStatus.EXPIRED:
            return attempt

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
        previous_attempt = (
            self.model._base_manager.select_for_update()
            .select_related("contract", "milestone")
            .get(pk=previous_attempt.pk)
        )

        return self.create_attempt(
            milestone=previous_attempt.milestone,
            idempotency_key=idempotency_key,
            initiated_by=initiated_by,
            provider=previous_attempt.provider,
            retry_of=previous_attempt,
            success_url=success_url or previous_attempt.success_url,
            failure_url=failure_url or previous_attempt.failure_url,
            provider_snapshot=previous_attempt.provider_snapshot,
            provider_status="",
        )