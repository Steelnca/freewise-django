from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from payments.gateways import get_payment_gateway
from payments.models import PaymentAttempt
from payments.services import (
    fail_payment_attempt,
    reconcile_payment_attempt_from_provider,
    settle_payment_attempt,
)

logger = logging.getLogger(__name__)


@transaction.atomic
def reconcile_attempt(attempt: PaymentAttempt) -> PaymentAttempt:
    """
    Reconcile one attempt against the provider's latest checkout status.
    """
    attempt = PaymentAttempt.objects.select_for_update().select_related(
        "contract",
        "milestone",
    ).get(pk=attempt.pk)

    if attempt.is_final:
        return attempt

    if not attempt.provider_checkout_id:
        return attempt

    gateway = get_payment_gateway(attempt.provider)

    try:
        snapshot = gateway.fetch_checkout(checkout_id=attempt.provider_checkout_id)
    except Exception:
        logger.exception(
            "Failed to fetch checkout from provider for attempt=%s",
            attempt.attempt_id,
        )
        return attempt

    status_value = gateway.normalize_status(snapshot.status)

    return reconcile_payment_attempt_from_provider(
        attempt=attempt,
        provider_status=status_value,
        provider_snapshot=snapshot.raw,
    )


@transaction.atomic
def reconcile_stale_attempts(*, minutes: int = 5, limit: int = 100) -> int:
    """
    Periodic recovery job for unresolved attempts.
    """
    cutoff = timezone.now() - timedelta(minutes=minutes)

    attempts = (
        PaymentAttempt.objects.open()
        .filter(updated_at__lte=cutoff)
        .exclude(provider_checkout_id="")
        .order_by("created_at")[:limit]
    )

    count = 0
    for attempt in attempts:
        try:
            reconcile_attempt(attempt)
            count += 1
        except Exception:
            logger.exception(
                "Failed to reconcile stale payment attempt=%s",
                attempt.attempt_id,
            )

    return count