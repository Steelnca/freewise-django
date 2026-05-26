"""
Freewise payment webhook helpers.

Keep provider webhook processing:
- small
- idempotent
- auditable
- safe to replay

This module is shared by:
- the live webhook endpoint
- the reconciliation management command
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Wallet, WalletTransaction, WebhookLog


class PaymentWebhookError(Exception):
    """
    Raised when a webhook cannot be processed safely.
    """


def _first_existing_key(payload: dict, keys: tuple[str, ...]) -> str:
    """
    Return the first non-empty string value found in payload for the given keys.
    """
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def extract_chargily_reference(payload: dict, fallback: str = "") -> str:
    """
    Extract a stable provider reference from a Chargily payload.

    Chargily payload keys may vary a bit depending on the event shape,
    so we check the common candidates in order.
    """
    return _first_existing_key(
        payload,
        ("invoice_id", "payment_id", "id"),
    ) or str(fallback or "").strip()


@transaction.atomic
def process_successful_payment(*, payment: WalletTransaction) -> WalletTransaction:
    """
    Mark a payment transaction as processed exactly once.

    For milestone checkout deposits, this also credits the wallet balance
    before the contract service moves the money into escrow.
    """
    payment = WalletTransaction.objects.select_for_update().select_related("wallet").get(
        pk=payment.pk
    )

    if payment.processed_at:
        return payment

    wallet = Wallet.objects.select_for_update().get(pk=payment.wallet_id)

    balance_before = wallet.available_balance
    balance_after = balance_before

    if payment.transaction_type == WalletTransaction.Type.DEPOSIT:
        balance_after = balance_before + payment.amount
        wallet.available_balance = balance_after
        wallet.save(update_fields=["available_balance", "updated_at"])

    payment.balance_before = balance_before
    payment.balance_after = balance_after
    payment.status = WalletTransaction.Status.COMPLETED
    payment.processed_at = timezone.now()
    payment.save(
        update_fields=[
            "status",
            "balance_before",
            "balance_after",
            "processed_at",
            "updated_at",
        ]
    )

    return payment


@transaction.atomic
def settle_successful_chargily_payment(*, payment: WalletTransaction) -> WalletTransaction:
    """
    Process a successful payment and, when it belongs to a milestone,
    forward it into the contract service so escrow is locked.
    """
    payment = process_successful_payment(payment=payment)

    if payment.reference_type != "milestone" or not payment.reference_id:
        return payment

    from contracts.models import Milestone
    from contracts.services import fund_milestone_from_payment

    milestone = Milestone.objects.select_related("contract").get(
        pk=payment.reference_id
    )

    fund_milestone_from_payment(
        milestone=milestone,
        idempotency_key=f"payment:{payment.pk}:escrow",
        provider_name=payment.provider_name,
        provider_reference=payment.provider_reference,
        initiated_by=payment.initiated_by,
        metadata={
            "payment_id": payment.pk,
            "provider_reference": payment.provider_reference,
        },
    )

    return payment


@transaction.atomic
def reconcile_chargily_webhook_log(*, webhook_log: WebhookLog) -> WebhookLog:
    """
    Reprocess a stored Chargily webhook log safely.
    """
    webhook_log = WebhookLog.objects.select_for_update().get(pk=webhook_log.pk)

    if webhook_log.processed:
        return webhook_log

    if not webhook_log.signature_valid:
        raise PaymentWebhookError(_("Webhook signature was not verified."))

    payload = webhook_log.payload or {}
    event_name = str(webhook_log.event_name or "").strip().lower()
    payload_status = str(payload.get("status") or event_name).strip().lower()

    if payload_status not in {"paid", "success", "succeeded", "completed"}:
        webhook_log.status = WebhookLog.Status.IGNORED
        webhook_log.processing_error = _("Webhook is not a successful payment event.")
        webhook_log.processed = True
        webhook_log.processed_at = timezone.now()
        webhook_log.save(
            update_fields=[
                "status",
                "processing_error",
                "processed",
                "processed_at",
                "updated_at",
            ]
        )
        return webhook_log

    provider_reference = extract_chargily_reference(
        payload,
        fallback=webhook_log.provider_event_id,
    )

    if not provider_reference:
        raise PaymentWebhookError(_("Missing provider reference."))

    payment = (
        WalletTransaction.objects.select_for_update()
        .filter(provider_reference=provider_reference)
        .first()
    )

    if not payment:
        raise PaymentWebhookError(_("Payment not found for this webhook."))

    settle_successful_chargily_payment(payment=payment)

    webhook_log.status = WebhookLog.Status.PROCESSED
    webhook_log.processed = True
    webhook_log.related_reference_type = payment.reference_type
    webhook_log.related_reference_id = payment.reference_id
    webhook_log.processing_error = ""
    webhook_log.processed_at = timezone.now()
    webhook_log.save(
        update_fields=[
            "status",
            "processed",
            "related_reference_type",
            "related_reference_id",
            "processing_error",
            "processed_at",
            "updated_at",
        ]
    )

    return webhook_log