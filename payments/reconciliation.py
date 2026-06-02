
from django.db import transaction

from payments.gateways import get_payment_gateway
from payments.models import PaymentAttempt
from payments.services import (
    settle_payment_attempt,
    fail_payment_attempt,
)


@transaction.atomic
def reconcile_attempt(attempt: PaymentAttempt):
    """
    Ask provider for latest truth.

    Used when:
    - webhook never arrived
    - provider delayed callback
    - manual admin recovery
    """

    if attempt.is_final:
        return attempt

    gateway = get_payment_gateway(attempt.provider)

    if not attempt.provider_checkout_id:
        return attempt

    checkout = gateway.fetch_checkout(
        checkout_id=attempt.provider_checkout_id,
    )

    status_value = gateway.normalize_status(
        checkout.status
    )

    if status_value == "paid":
        return settle_payment_attempt(
            attempt=attempt,
            provider_status=status_value,
            provider_snapshot=checkout.raw,
        )

    if status_value in {
        "failed",
        "cancelled",
        "expired",
    }:
        return fail_payment_attempt(
            attempt=attempt,
            reason="Provider reports failed checkout.",
            provider_status=status_value,
            provider_snapshot=checkout.raw,
        )

    attempt.provider_status = status_value
    attempt.provider_snapshot = checkout.raw

    attempt.save(
        update_fields=[
            "provider_status",
            "provider_snapshot",
            "updated_at",
        ]
    )

    return attempt


def reconcile_pending_attempts(limit=100):
    """
    Periodic recovery task.

    Runs every few minutes.
    """

    attempts = (
        PaymentAttempt.objects
        .stale(minutes=5)
        .order_by("created_at")[:limit]
    )

    recovered = 0

    for attempt in attempts:
        reconcile_attempt(attempt)
        recovered += 1

    return recovered