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

from .models import WalletTransaction, WebhookLog


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

    The provider payload shape can vary a bit, so we check a few likely keys.
    """
    return _first_existing_key(
        payload,
        (
            "invoice_id",
            "payment_id",
            "id",
        ),
    ) or fallback


@transaction.atomic
def process_successful_payment(*, payment: WalletTransaction) -> WalletTransaction:
    """
    Mark a payment transaction as processed exactly once.

    This keeps the replay path safe even if the webhook is delivered multiple times.
    """
    payment = WalletTransaction.objects.select_for_update().get(pk=payment.pk)

    if payment.processed_at:
        return payment

    # Try to move the status to a terminal success value if the model supports it.
    status_choices = getattr(payment.__class__, "Status", None)
    success_value = None
    if status_choices is not None:
        for candidate in ("COMPLETED", "SUCCEEDED", "PAID"):
            if hasattr(status_choices, candidate):
                success_value = getattr(status_choices, candidate)
                break

    if success_value is not None and getattr(payment, "status", None) != success_value:
        payment.status = success_value

    payment.processed_at = timezone.now()

    update_fields = ["processed_at", "updated_at"]
    if success_value is not None:
        update_fields.insert(0, "status")

    payment.save(update_fields=update_fields)
    return payment


@transaction.atomic
def reconcile_chargily_webhook_log(*, webhook_log: WebhookLog) -> WebhookLog:
    """
    Reprocess a stored Chargily webhook log safely.

    Rules:
    - only processed once
    - only trusted logs (signature_valid=True)
    - only paid events trigger payment processing
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

    process_successful_payment(payment=payment)

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