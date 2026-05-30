
"""
Freewise contract services.

This is the only place where contract and milestone state should change.

Rules:
- views should call these helpers
- payment hooks should call these helpers
- no direct milestone.status edits in random places
"""

from __future__ import annotations

from typing import Optional
from decimal import Decimal


from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from payments.models import EscrowHold
from payments.services import (
    DEFAULT_CURRENCY as PAYMENTS_DEFAULT_CURRENCY,
    EscrowHoldError,
    get_or_create_wallet_for_user,
    hold_funds_for_escrow,
    release_escrow_hold_to_wallet,
    refund_escrow_hold,
)

from .models import Contract, Milestone


TERMINAL_MILESTONE_STATUSES = {
    Milestone.Status.RELEASED,
    Milestone.Status.REFUNDED,
    Milestone.Status.CANCELLED,
}


def contract_reference_for_milestone(milestone: Milestone) -> str:
    """
    Stable reference string used by payments. Keep it predictable.
    """
    return f"contract:{milestone.contract_id}:milestone:{milestone.id}"


def get_user_contracts_queryset(user):
    """
    Return contracts where the authenticated user is a party.
    """
    account = getattr(user, "account", None)
    client = getattr(account, "client_profile", None)
    freelancer = getattr(account, "freelancer_profile", None)

    qs = (
        Contract.objects.select_related(
            "client__account__user",
            "freelancer__account__user",
            "job",
            "proposal",
        )
        .prefetch_related("milestones")
        .distinct()
    )

    if client and freelancer:
        return qs.filter(Q(client=client) | Q(freelancer=freelancer))
    if client:
        return qs.filter(client=client)
    if freelancer:
        return qs.filter(freelancer=freelancer)
    return Contract.objects.none()


def get_party_contract_queryset(user, pk: Optional[int] = None):
    """
    Shared access helper for list/detail views.
    """
    qs = get_user_contracts_queryset(user)
    if pk is not None:
        qs = qs.filter(pk=pk)
    return qs


def ensure_party_access(contract: Contract, user) -> None:
    """
    Enforce that only the two contract parties can touch it.
    """
    account = getattr(user, "account", None)
    client = getattr(account, "client_profile", None)
    freelancer = getattr(account, "freelancer_profile", None)

    if client and contract.client_id == client.id:
        return
    if freelancer and contract.freelancer_id == freelancer.id:
        return

    raise PermissionDenied(_("You are not allowed to access this contract."))


def ensure_milestone_access(milestone: Milestone, user) -> None:
    """
    Enforce access through the parent contract.
    """
    ensure_party_access(milestone.contract, user)


def _set_contract_state(
    contract: Contract,
    status: str,
    *,
    timestamp_field: Optional[str] = None,
    note: Optional[str] = None,
) -> Contract:
    """
    Central state updater for contracts.
    """
    contract.status = status
    if timestamp_field:
        setattr(contract, timestamp_field, timezone.now())
    if note:
        contract.notes = note
    contract.full_clean()
    contract.save()
    return contract


def _all_milestones_terminal(contract: Contract) -> bool:
    return not contract.milestones.exclude(status__in=TERMINAL_MILESTONE_STATUSES).exists()


def _maybe_finish_contract(contract: Contract) -> Contract:
    """
    Mark the contract as released when all milestones are terminal.
    """
    if _all_milestones_terminal(contract):
        return _set_contract_state(
            contract,
            Contract.Status.RELEASED,
            timestamp_field="completed_at",
        )
    return contract

def _milestones_total(contract):
    return sum((m.amount for m in contract.milestones.all()), Decimal("0.00"))



@transaction.atomic
def fund_milestone_from_payment(
    *,
    milestone: Milestone,
    idempotency_key: str,
    provider_name: str = "",
    provider_reference: str = "",
    initiated_by=None,
    metadata: Optional[dict] = None,
) -> Milestone:
    """
    Called after a payment succeeds.

    It locks escrow for this milestone and moves the milestone into FUNDED.
    """
    milestone = Milestone.objects.select_for_update().select_related(
        "contract",
        "contract__client__account__user",
    ).get(pk=milestone.pk)

    contract = milestone.contract
    wallet = get_or_create_wallet_for_user(
        contract.client.account.user,
        currency=contract.currency or PAYMENTS_DEFAULT_CURRENCY,
    )

    if milestone.status != Milestone.Status.PENDING:
        raise ValidationError(
            {"status": _("Only pending milestones can be funded.")}
        )

    hold_funds_for_escrow(
        wallet=wallet,
        amount=milestone.amount,
        contract_reference=contract_reference_for_milestone(milestone),
        idempotency_key=idempotency_key,
        initiated_by=initiated_by,
        provider_name=provider_name,
        provider_reference=provider_reference,
        reference_type="milestone",
        reference_id=str(milestone.pk),
        description=_("Funds moved into escrow for milestone funding."),
        metadata=metadata or {},
    )

    milestone.status = Milestone.Status.FUNDED
    milestone.submitted_at = None
    milestone.approved_at = None
    milestone.released_at = None
    milestone.refunded_at = None
    milestone.disputed_at = None
    milestone.full_clean()
    milestone.save()

    if contract.status in {Contract.Status.DRAFT, Contract.Status.PENDING_FUNDING}:
        _set_contract_state(
            contract,
            Contract.Status.FUNDED,
            timestamp_field="funded_at",
        )

    return milestone


@transaction.atomic
def submit_milestone(
    *,
    milestone: Milestone,
    user,
    submission_note: str = "",
    submission_link: str = "",
) -> Milestone:
    """
    Freelancer submits work for review.
    """
    ensure_milestone_access(milestone, user)

    account = getattr(user, "account", None)
    freelancer = getattr(account, "freelancer_profile", None)
    if not freelancer or milestone.contract.freelancer_id != freelancer.id:
        raise PermissionDenied(_("Only the assigned freelancer can submit this milestone."))

    milestone = Milestone.objects.select_for_update().select_related("contract").get(
        pk=milestone.pk
    )

    if milestone.status not in {
        Milestone.Status.FUNDED,
        Milestone.Status.REVISION_REQUESTED,
    }:
        raise ValidationError(
            {"status": _("This milestone cannot be submitted in its current state.")}
        )

    milestone.status = Milestone.Status.SUBMITTED
    milestone.submission_link = submission_link or milestone.submission_link
    milestone.submission_note = submission_note or milestone.submission_note
    milestone.submitted_at = timezone.now()
    milestone.full_clean()
    milestone.save()

    contract = milestone.contract
    if contract.status in {Contract.Status.FUNDED, Contract.Status.ACTIVE}:
        _set_contract_state(contract, Contract.Status.SUBMITTED, timestamp_field="submitted_at")

    return milestone


@transaction.atomic
def request_revision(
    *,
    milestone: Milestone,
    user,
    review_note: str = "",
) -> Milestone:
    """
    Client asks for changes before approval.
    """
    ensure_milestone_access(milestone, user)

    account = getattr(user, "account", None)
    client = getattr(account, "client_profile", None)
    if not client or milestone.contract.client_id != client.id:
        raise PermissionDenied(_("Only the client can request a revision."))

    milestone = Milestone.objects.select_for_update().select_related("contract").get(
        pk=milestone.pk
    )

    if milestone.status != Milestone.Status.SUBMITTED:
        raise ValidationError(
            {"status": _("Only submitted milestones can be revised.")}
        )

    milestone.status = Milestone.Status.REVISION_REQUESTED
    milestone.review_note = review_note or milestone.review_note
    milestone.full_clean()
    milestone.save()

    _set_contract_state(milestone.contract, Contract.Status.REVISION_REQUESTED)
    return milestone


@transaction.atomic
def approve_milestone(
    *,
    milestone: Milestone,
    user,
    review_note: str = "",
    fee_amount=0,
) -> Milestone:
    """
    Client approves the milestone and triggers escrow release.
    """
    ensure_milestone_access(milestone, user)

    account = getattr(user, "account", None)
    client = getattr(account, "client_profile", None)
    if not client or milestone.contract.client_id != client.id:
        raise PermissionDenied(_("Only the client can approve this milestone."))

    milestone = Milestone.objects.select_for_update().select_related(
        "contract",
        "contract__freelancer__account__user",
    ).get(pk=milestone.pk)

    if milestone.status != Milestone.Status.SUBMITTED:
        raise ValidationError(
            {"status": _("Only submitted milestones can be approved.")}
        )

    hold = EscrowHold.objects.select_for_update().filter(
        contract_reference=contract_reference_for_milestone(milestone),
        status=EscrowHold.Status.ACTIVE,
    ).first()

    if not hold:
        raise EscrowHoldError(_("Escrow hold not found for this milestone."))

    recipient_wallet = get_or_create_wallet_for_user(
        milestone.contract.freelancer.account.user,
        currency=milestone.contract.currency or PAYMENTS_DEFAULT_CURRENCY,
    )

    release_escrow_hold_to_wallet(
        hold=hold,
        recipient_wallet=recipient_wallet,
        idempotency_key=f"milestone:{milestone.pk}:release",
        initiated_by=user,
        amount=milestone.amount,
        fee_amount=fee_amount,
        reference_type="milestone",
        reference_id=str(milestone.pk),
        description=_("Milestone approved and escrow released."),
        metadata={
            "contract_id": milestone.contract_id,
            "milestone_id": milestone.pk,
        },
    )

    milestone.status = Milestone.Status.APPROVED
    milestone.review_note = review_note or milestone.review_note
    milestone.approved_at = timezone.now()
    milestone.released_at = timezone.now()
    milestone.full_clean()
    milestone.save()

    contract = milestone.contract
    if _all_milestones_terminal(contract):
        _set_contract_state(
            contract,
            Contract.Status.RELEASED,
            timestamp_field="completed_at",
        )
    else:
        _set_contract_state(contract, Contract.Status.APPROVED)

    return milestone


@transaction.atomic
def open_dispute(
    *,
    milestone: Milestone,
    user,
    dispute_reason: str = "",
) -> Milestone:
    """
    Open a dispute and freeze the milestone until moderation resolves it.
    """
    ensure_milestone_access(milestone, user)

    account = getattr(user, "account", None)
    client = getattr(account, "client_profile", None)
    if not client or milestone.contract.client_id != client.id:
        raise PermissionDenied(_("Only the client can open a dispute from this endpoint."))

    milestone = Milestone.objects.select_for_update().select_related("contract").get(
        pk=milestone.pk
    )

    if milestone.status not in {
        Milestone.Status.SUBMITTED,
        Milestone.Status.REVISION_REQUESTED,
        Milestone.Status.APPROVED,
    }:
        raise ValidationError(
            {"status": _("This milestone cannot be disputed in its current state.")}
        )

    hold = EscrowHold.objects.select_for_update().filter(
        contract_reference=contract_reference_for_milestone(milestone)
    ).first()

    if hold and hold.status == EscrowHold.Status.ACTIVE:
        hold.status = EscrowHold.Status.DISPUTED
        hold.resolution_note = dispute_reason or _("Milestone disputed.")
        hold.save(update_fields=["status", "resolution_note", "updated_at"])

    milestone.status = Milestone.Status.DISPUTED
    milestone.dispute_reason = dispute_reason or milestone.dispute_reason
    milestone.disputed_at = timezone.now()
    milestone.full_clean()
    milestone.save()

    _set_contract_state(
        milestone.contract,
        Contract.Status.DISPUTED,
        timestamp_field="disputed_at",
        note=dispute_reason or milestone.contract.notes,
    )

    return milestone


@transaction.atomic
def refund_milestone(
    *,
    milestone: Milestone,
    user,
    refund_note: str = "",
) -> Milestone:
    """
    Refund a disputed or cancelled milestone back to the client wallet.
    """
    ensure_milestone_access(milestone, user)

    milestone = Milestone.objects.select_for_update().select_related(
        "contract",
        "contract__client__account__user",
    ).get(pk=milestone.pk)

    hold = EscrowHold.objects.select_for_update().filter(
        contract_reference=contract_reference_for_milestone(milestone),
        status__in=[EscrowHold.Status.ACTIVE, EscrowHold.Status.DISPUTED],
    ).first()

    if not hold:
        raise EscrowHoldError(_("Escrow hold not found for this milestone."))

    refund_escrow_hold(
        hold=hold,
        idempotency_key=f"milestone:{milestone.pk}:refund",
        initiated_by=user,
        amount=milestone.amount,
        reference_type="milestone",
        reference_id=str(milestone.pk),
        description=refund_note or _("Milestone refunded."),
        metadata={
            "contract_id": milestone.contract_id,
            "milestone_id": milestone.pk,
        },
    )

    milestone.status = Milestone.Status.REFUNDED
    milestone.refunded_at = timezone.now()
    milestone.full_clean()
    milestone.save()

    contract = milestone.contract
    if _all_milestones_terminal(contract):
        _set_contract_state(
            contract,
            Contract.Status.REFUNDED,
            timestamp_field="completed_at",
        )

    return milestone


@transaction.atomic
def cancel_contract(*, contract: Contract, user, reason: str = "") -> Contract:
    """
    Cancel a contract when no active delivery should continue.
    """
    ensure_party_access(contract, user)

    contract = Contract.objects.select_for_update().get(pk=contract.pk)
    if contract.status in {Contract.Status.RELEASED, Contract.Status.REFUNDED}:
        raise ValidationError({"status": _("Completed contracts cannot be cancelled.")})

    contract.status = Contract.Status.CANCELLED
    contract.cancelled_at = timezone.now()
    if reason:
        contract.notes = reason
    contract.full_clean()
    contract.save()
    return contract


@transaction.atomic
def create_milestone(*, contract, user, title, description, amount, due_date, order):
    ensure_party_access(contract, user)

    account = getattr(user, "account", None)
    client = getattr(account, "client_profile", None)
    if not client or contract.client_id != client.id:
        raise PermissionDenied("Only the client can create milestones.")

    contract = Contract.objects.select_for_update().get(pk=contract.pk)

    if contract.status != Contract.Status.PENDING_FUNDING:
        raise ValidationError({"detail": "Milestones can only be edited before funding starts."})

    amount = Decimal(str(amount))

    current_total = _milestones_total(contract)
    if current_total + amount > contract.agreed_price:
        raise ValidationError({"amount": "Milestones cannot exceed the agreed contract price."})

    milestone = Milestone.objects.create(
        contract=contract,
        title=title,
        description=description,
        amount=amount,
        due_date=due_date,
        order=order,
        status=Milestone.Status.PENDING,
    )
    return milestone