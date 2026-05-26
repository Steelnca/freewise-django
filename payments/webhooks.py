
"""
Freewise payment webhook services.

Provider webhooks should stay thin and idempotent.
Business logic belongs here.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from contracts.models import Milestone
from contracts.services import fund_milestone_from_payment

from .models import PaymentTransaction


class PaymentWebhookError(Exception):
    """
    Raised when webhook processing fails safely.
    """


@transaction.atomic
def process_successful_payment(*, payment: PaymentTransaction):
    """
    Process a successful provider payment.

    This function is idempotent:
    calling it multiple times will not duplicate escrow holds.
    """

    payment = (
        PaymentTransaction.objects
        .select_for_update()
        .select_related("initiated_by")
        .get(pk=payment.pk)
    )

    # Already processed safely.
    if payment.processed_at:
        return payment

    if payment.status == PaymentTransaction.PaymentStatus.SUCCEEDED:
        payment.processed_at = timezone.now()
        payment.save(update_fields=["processed_at"])
        return payment

    if payment.reference_type != "milestone":
        raise PaymentWebhookError(
            _("Unsupported payment reference type.")
        )

    try:
        milestone = (
            Milestone.objects
            .select_related(
                "contract",
                "contract__client__account__user",
            )
            .get(pk=payment.reference_id)
        )
    except Milestone.DoesNotExist:
        raise PaymentWebhookError(
            _("Referenced milestone does not exist.")
        )

    fund_milestone_from_payment(
        milestone=milestone,
        idempotency_key=f"payment:{payment.pk}:fund",
        provider_name=payment.provider_name,
        provider_reference=payment.provider_reference,
        initiated_by=payment.initiated_by,
        metadata={
            "payment_id": payment.pk,
        },
    )

    payment.status = PaymentTransaction.PaymentStatus.SUCCEEDED
    payment.processed_at = timezone.now()

    payment.save(
        update_fields=[
            "status",
            "processed_at",
            "updated_at",
        ]
    )

    return payment