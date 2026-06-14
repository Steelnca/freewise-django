"""
Freewise contract services.

This is the only place where contract and milestone state should change.

Rules:
- views should call these helpers
- payment hooks should call these helpers
- no direct milestone.status edits in random places
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from payments.models import EscrowHold, WalletTransaction
from payments.services import (
    DEFAULT_CURRENCY as PAYMENTS_DEFAULT_CURRENCY,
    EscrowHoldError,
    calculate_platform_fee,
    get_or_create_platform_wallet,
    get_or_create_wallet_for_user,
    hold_funds_for_escrow,
    release_escrow_hold_to_wallet,
    refund_escrow_hold,
    normalize_money,
    milestone_has_settled_or_paid_payment,
)

from .models import Contract, Milestone, ContractEvent, MilestoneSubmission, MilestonePlan, MilestonePlanItem
from .constants import MAX_FIRST_MILESTONE_PERCENT, MAX_MILESTONES, MIN_LAST_MILESTONE_PERCENT

TERMINAL_MILESTONE_STATUSES = {
    Milestone.Status.RELEASED,
    Milestone.Status.REFUNDED,
}

def contract_reference_for_milestone(milestone: Milestone) -> str:
    """
    Stable reference string used by payments. Keep it predictable.
    """
    return f"contract:{milestone.contract.public_id}:milestone:{milestone.public_id}"


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
    if note is not None:
        contract.notes = note

    contract.full_clean()
    contract.save()
    return contract


def _milestones_total(contract):
    return sum((m.amount for m in contract.milestones.all()), Decimal("0.00"))


def log_contract_event(*, contract: Contract, event_type: str, actor=None, metadata=None) -> None:
    """
    Write one immutable contract activity record.
    """
    ContractEvent.objects.create(
        contract=contract,
        event_type=event_type,
        actor=actor,
        metadata=metadata or {},
    )


def _get_milestone_hold(milestone: Milestone):
    """
    Look up the escrow hold tied to a milestone contract reference.
    """
    return (
        EscrowHold.objects.select_for_update()
        .filter(contract_reference=contract_reference_for_milestone(milestone))
        .first()
    )


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

    if contract.status in {
        Contract.Status.CANCELLED,
        Contract.Status.WITHDRAWN,
        Contract.Status.SUSPENDED,
        Contract.Status.COMPLETED,
    }:
        raise ValidationError({"status": _("This contract is not accepting funding.")})

    first_pending = (
        contract.milestones.filter(status=Milestone.Status.PENDING)
        .order_by("order", "created_at")
        .first()
    )
    if first_pending and first_pending.pk != milestone.pk:
        raise ValidationError({"status": _("Fund the first pending milestone in order.")})

    wallet = get_or_create_wallet_for_user(
        contract.client.account.user,
        currency=contract.currency or PAYMENTS_DEFAULT_CURRENCY,
    )

    if milestone.status != Milestone.Status.PENDING:
        raise ValidationError({"status": _("Only pending milestones can be funded.")})

    hold_funds_for_escrow(
        wallet=wallet,
        amount=milestone.amount,
        contract_reference=contract_reference_for_milestone(milestone),
        idempotency_key=idempotency_key,
        initiated_by=initiated_by,
        provider_name=provider_name,
        provider_reference=provider_reference,
        reference_type="milestone",
        reference_id=str(milestone.public_id),
        description=_("Funds moved into escrow for milestone funding."),
        metadata=metadata or {},
    )

    milestone.status = Milestone.Status.FUNDED
    milestone.funded_at = timezone.now()
    milestone.submitted_at = None
    milestone.approved_at = None
    milestone.released_at = None
    milestone.refunded_at = None
    milestone.disputed_at = None
    milestone.full_clean()
    milestone.save()

    log_contract_event(
        contract=contract,
        event_type=ContractEvent.ContractEventType.MILESTONE_FUNDED,
        actor=initiated_by,
        metadata={"milestone_public_id": milestone.public_id},
    )

    if contract.status in {Contract.Status.DRAFT, Contract.Status.PENDING_FUNDING}:
        _set_contract_state(
            contract,
            Contract.Status.IN_PROGRESS,
            timestamp_field="active_at",
        )

    return milestone


@transaction.atomic
def submit_milestone(
    *,
    milestone: Milestone,
    user,
    submission_note: str = "",
    submission_link: str = "",
    payload: dict = {},
) -> MilestoneSubmission:
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

    if milestone.contract.status in {
        Contract.Status.CANCELLED,
        Contract.Status.WITHDRAWN,
        Contract.Status.SUSPENDED,
        Contract.Status.COMPLETED,
    }:
        raise ValidationError({"status": _("This contract is not accepting submissions.")})

    if milestone.status not in {
        Milestone.Status.FUNDED,
        Milestone.Status.REVISION_REQUESTED,
    }:
        raise ValidationError({"status": _("This milestone cannot be submitted in its current state.")})

    if not milestone_has_settled_or_paid_payment(milestone=milestone):
        raise ValidationError({"status": _("This milestone has no settled or completed payment attempts.")})

    submission = MilestoneSubmission.objects.create(
        milestone=milestone,
        submitted_by=user,
        note=submission_note,
        external_link=submission_link,
        payload=payload or {},
    )

    milestone.status = Milestone.Status.SUBMITTED
    milestone.submission_link = submission_link or milestone.submission_link
    milestone.submission_note = submission_note or milestone.submission_note
    milestone.submitted_at = timezone.now()
    milestone.full_clean()
    milestone.save()

    log_contract_event(
        contract=milestone.contract,
        event_type=ContractEvent.ContractEventType.MILESTONE_SUBMITTED,
        actor=user,
        metadata={"milestone_public_id": milestone.public_id},
    )

    return submission


@transaction.atomic
def request_revision(
    *,
    milestone: Milestone,
    user,
    revision_note: str = "",
    revision_scope: str = "",
) -> Milestone:
    """
    Client asks for changes on a submitted milestone.
    """
    ensure_milestone_access(milestone, user)

    account = getattr(user, "account", None)
    client = getattr(account, "client_profile", None)
    if not client or milestone.contract.client_id != client.id:
        raise PermissionDenied(_("Only the client can request a revision."))

    milestone = Milestone.objects.select_for_update().select_related("contract").get(pk=milestone.pk)

    if milestone.contract.status in {
        Contract.Status.CANCELLED,
        Contract.Status.WITHDRAWN,
        Contract.Status.SUSPENDED,
        Contract.Status.COMPLETED,
    }:
        raise ValidationError({"status": _("This contract is not accepting revisions.")})

    if milestone.status != Milestone.Status.SUBMITTED:
        raise ValidationError({"status": _("Only submitted milestones can be revised.")})

    milestone.status = Milestone.Status.REVISION_REQUESTED
    milestone.revision_note = revision_note or milestone.revision_note
    milestone.revision_scope = revision_scope or milestone.revision_scope
    milestone.revision_requested_at = timezone.now()
    milestone.full_clean()
    milestone.save()

    log_contract_event(
        contract=milestone.contract,
        event_type=ContractEvent.ContractEventType.MILESTONE_REVISION_REQUESTED,
        actor=user,
        metadata={
            "milestone_public_id": milestone.public_id,
            "revision_scope": revision_scope,
            "revision_note": revision_note,
        },
    )

    return milestone


@transaction.atomic
def approve_milestone(
    *,
    milestone: Milestone,
    user,
    review_note: str = "",
) -> Milestone:
    """
    Client accepts delivered work.

    Escrow is released.
    Platform fee is deducted.
    Milestone becomes RELEASED.
    Contract may become COMPLETED.
    """
    ensure_milestone_access(milestone, user)

    account = getattr(user, "account", None)
    client = getattr(account, "client_profile", None)
    if not client or milestone.contract.client_id != client.id:
        raise PermissionDenied(_("Only the client can approve this milestone."))

    milestone = (
        Milestone.objects
        .select_for_update()
        .select_related(
            "contract",
            "contract__client",
            "contract__freelancer",
        )
        .get(pk=milestone.pk)
    )

    if milestone.contract.status in {
        Contract.Status.CANCELLED,
        Contract.Status.WITHDRAWN,
        Contract.Status.SUSPENDED,
        Contract.Status.COMPLETED,
    }:
        raise ValidationError({"status": _("This contract cannot be approved right now.")})

    if milestone.status != Milestone.Status.SUBMITTED:
        raise ValidationError({"status": _("Only submitted milestones can be approved.")})

    if not milestone_has_settled_or_paid_payment(milestone=milestone):
        raise ValidationError({"status": _("This milestone has no settled or completed payment attempts.")})

    contract = milestone.contract

    hold = _get_milestone_hold(milestone)
    if not hold:
        raise EscrowHoldError(_("Escrow hold not found for this milestone."))

    freelancer_wallet = get_or_create_wallet_for_user(
        contract.freelancer.account.user,
        currency=contract.currency or PAYMENTS_DEFAULT_CURRENCY,
    )
    fee_wallet = get_or_create_platform_wallet(contract.currency or PAYMENTS_DEFAULT_CURRENCY)

    gross_amount = normalize_money(milestone.amount)
    platform_fee_amount = calculate_platform_fee(gross_amount)
    net_amount = normalize_money(gross_amount - platform_fee_amount)

    release_escrow_hold_to_wallet(
        hold=hold,
        recipient_wallet=freelancer_wallet,
        idempotency_key=f"milestone:{milestone.public_id}:approve",
        initiated_by=user,
        release_amount=net_amount,
        fee_wallet=fee_wallet,
        fee_amount=platform_fee_amount,
        reference_type="milestone",
        reference_id=str(milestone.public_id),
        description=f"Milestone #{milestone.public_id} approved.",
        metadata={
            "contract_public_id": milestone.contract.public_id,
            "milestone_public_id": milestone.public_id,
            "fee_amount": str(platform_fee_amount),
        },
    )

    milestone.status = Milestone.Status.RELEASED
    milestone.approved_at = timezone.now()
    milestone.released_at = timezone.now()
    if review_note:
        milestone.review_note = review_note

    milestone.full_clean()
    milestone.save(
        update_fields=[
            "status",
            "approved_at",
            "released_at",
            "review_note",
            "updated_at",
        ]
    )

    log_contract_event(
        contract=contract,
        actor=user,
        event_type=ContractEvent.ContractEventType.MILESTONE_APPROVED,
        metadata={
            "milestone_public_id": milestone.public_id,
            "amount": str(milestone.amount),
            "fee_amount": str(platform_fee_amount),
        },
    )

    sync_contract_completion(contract)

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

    if milestone.contract.status in {
        Contract.Status.CANCELLED,
        Contract.Status.WITHDRAWN,
        Contract.Status.COMPLETED,
    }:
        raise ValidationError({"status": _("This contract cannot be disputed.")})

    if milestone.status not in {
        Milestone.Status.SUBMITTED,
        Milestone.Status.REVISION_REQUESTED,
    }:
        raise ValidationError({"status": _("This milestone cannot be disputed in its current state.")})

    hold = _get_milestone_hold(milestone)

    if hold and hold.status == EscrowHold.Status.ACTIVE:
        hold.status = EscrowHold.Status.DISPUTED
        hold.resolution_note = dispute_reason or _("Milestone disputed.")
        hold.save(update_fields=["status", "resolution_note", "updated_at"])

    milestone.status = Milestone.Status.DISPUTED
    milestone.dispute_reason = dispute_reason or milestone.dispute_reason
    milestone.disputed_at = timezone.now()
    milestone.full_clean()
    milestone.save()

    log_contract_event(
        contract=milestone.contract,
        event_type=ContractEvent.ContractEventType.MILESTONE_DISPUTED,
        actor=user,
        metadata={"milestone_public_id": milestone.public_id},
    )

    _set_contract_state(
        milestone.contract,
        Contract.Status.SUSPENDED,
        timestamp_field="suspended_at",
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

    hold = _get_milestone_hold(milestone)
    if not hold:
        raise EscrowHoldError(_("Escrow hold not found for this milestone."))

    refund_escrow_hold(
        hold=hold,
        idempotency_key=f"milestone:{milestone.public_id}:refund",
        initiated_by=user,
        amount=milestone.amount,
        reference_type="milestone",
        reference_id=str(milestone.public_id),
        description=refund_note or _("Milestone refunded."),
        metadata={
            "contract_public_id": milestone.contract.public_id,
            "milestone_public_id": milestone.public_id,
        },
    )

    milestone.status = Milestone.Status.REFUNDED
    milestone.refunded_at = timezone.now()
    milestone.full_clean()
    milestone.save()

    log_contract_event(
        contract=milestone.contract,
        event_type=ContractEvent.ContractEventType.MILESTONE_REFUNDED,
        actor=user,
        metadata={"milestone_public_id": milestone.public_id, "refund_note": refund_note},
    )

    sync_contract_completion(milestone.contract)

    return milestone


@transaction.atomic
def cancel_contract(*, contract: Contract, user, reason: str = "") -> Contract:
    """
    Cancel a contract when no active delivery should continue.

    If the contract already started, we treat the stop as WITHDRAWN and
    refund any still-held escrowed milestones.
    """
    ensure_party_access(contract, user)

    contract = (
        Contract.objects.select_for_update()
        .prefetch_related("milestones")
        .get(pk=contract.pk)
    )

    if contract.status in {
        Contract.Status.COMPLETED,
        Contract.Status.CANCELLED,
        Contract.Status.WITHDRAWN,
    }:
        raise ValidationError({"status": _("This contract is already finished.")})

    if contract.milestones.filter(status=Milestone.Status.RELEASED).exists():
        raise ValidationError({"status": _("Released contracts cannot be cancelled.")})

    started = contract.milestones.exclude(status=Milestone.Status.PENDING).exists()

    if started:
        refundable_statuses = {
            Milestone.Status.FUNDED,
            Milestone.Status.SUBMITTED,
            Milestone.Status.REVISION_REQUESTED,
            Milestone.Status.DISPUTED,
        }

        for milestone in contract.milestones.filter(status__in=refundable_statuses).order_by("order", "created_at"):
            hold = _get_milestone_hold(milestone)
            if hold and hold.status in {EscrowHold.Status.ACTIVE, EscrowHold.Status.DISPUTED}:
                refund_escrow_hold(
                    hold=hold,
                    idempotency_key=f"milestone:{milestone.public_id}:cancel-refund",
                    initiated_by=user,
                    amount=milestone.amount,
                    reference_type="milestone",
                    reference_id=str(milestone.public_id),
                    description=reason or _("Contract withdrawn and milestone refunded."),
                    metadata={
                        "contract_public_id": milestone.contract.public_id,
                        "milestone_public_id": milestone.public_id,
                        "reason": reason,
                    },
                )

            milestone.status = Milestone.Status.REFUNDED
            milestone.refunded_at = timezone.now()
            milestone.full_clean()
            milestone.save(update_fields=["status", "refunded_at", "updated_at"])

            log_contract_event(
                contract=contract,
                event_type=ContractEvent.ContractEventType.MILESTONE_REFUNDED,
                actor=user,
                metadata={"milestone_public_id": milestone.public_id, "reason": reason},
            )

        _set_contract_state(
            contract,
            Contract.Status.WITHDRAWN,
            timestamp_field="withdrawn_at",
            note=reason or contract.notes,
        )

        log_contract_event(
            contract=contract,
            event_type=ContractEvent.ContractEventType.CONTRACT_WITHDRAWN,
            actor=user,
            metadata={"reason": reason},
        )
        return contract

    _set_contract_state(
        contract,
        Contract.Status.CANCELLED,
        timestamp_field="cancelled_at",
        note=reason or contract.notes,
    )

    log_contract_event(
        contract=contract,
        event_type=ContractEvent.ContractEventType.CONTRACT_CANCELLED,
        actor=user,
        metadata={"reason": reason},
    )

    return contract


@transaction.atomic
def create_milestone(*, contract, user, title, description, amount, due_date, order):
    ensure_party_access(contract, user)

    account = getattr(user, "account", None)
    client = getattr(account, "client_profile", None)
    if not client or contract.client_id != client.id:
        raise PermissionDenied(_("Only the client can create milestones."))

    contract = Contract.objects.select_for_update().get(pk=contract.pk)

    if contract.status != Contract.Status.PENDING_FUNDING:
        raise ValidationError({"detail": _("Milestones can only be edited before funding starts.")})

    amount = Decimal(str(amount))
    current_total = sum((m.amount for m in contract.milestones.all()), Decimal("0.00"))
    if current_total + amount > contract.agreed_price:
        raise ValidationError({"amount": _("Milestones cannot exceed the agreed contract price.")})

    milestone = Milestone.objects.create(
        contract=contract,
        title=title,
        description=description,
        amount=amount,
        due_date=due_date,
        order=order,
        status=Milestone.Status.PENDING,
    )

    log_contract_event(
        contract=contract,
        event_type=ContractEvent.ContractEventType.MILESTONE_CREATED,
        actor=user,
        metadata={"milestone_public_id": milestone.public_id},
    )

    return milestone


@transaction.atomic
def sync_contract_completion(contract: Contract) -> Contract:
    """
    Keep the contract status aligned with the milestone lifecycle.

    - Disputed milestone -> SUSPENDED
    - All pending -> PENDING_FUNDING
    - Any funded/submitted/revision-requested/released -> IN_PROGRESS
    - All terminal and any refunded -> WITHDRAWN
    - All terminal and no refund -> COMPLETED
    """
    contract = Contract.objects.select_for_update().get(pk=contract.pk)

    if contract.status in {
        Contract.Status.CANCELLED,
        Contract.Status.WITHDRAWN,
        Contract.Status.COMPLETED,
    }:
        return contract

    milestones = contract.milestones.all()

    if milestones.filter(status=Milestone.Status.DISPUTED).exists():
        if contract.status != Contract.Status.SUSPENDED:
            _set_contract_state(
                contract,
                Contract.Status.SUSPENDED,
                timestamp_field="suspended_at",
            )
        return contract

    if not milestones.exists():
        return contract

    if milestones.filter(status=Milestone.Status.PENDING).count() == milestones.count():
        if contract.status != Contract.Status.PENDING_FUNDING:
            _set_contract_state(contract, Contract.Status.PENDING_FUNDING)
        return contract

    active_statuses = {
        Milestone.Status.FUNDED,
        Milestone.Status.SUBMITTED,
        Milestone.Status.REVISION_REQUESTED,
        Milestone.Status.RELEASED,
    }

    any_active = milestones.filter(status__in=active_statuses).exists()
    any_refunded = milestones.filter(status=Milestone.Status.REFUNDED).exists()
    all_terminal = not milestones.exclude(status__in=TERMINAL_MILESTONE_STATUSES).exists()

    if all_terminal:
        final_status = Contract.Status.WITHDRAWN if any_refunded else Contract.Status.COMPLETED
        timestamp_field = "withdrawn_at" if final_status == Contract.Status.WITHDRAWN else "completed_at"
        if contract.status != final_status:
            _set_contract_state(contract, final_status, timestamp_field=timestamp_field)
        return contract

    if any_refunded and not any_active:
        if contract.status != Contract.Status.WITHDRAWN:
            _set_contract_state(
                contract,
                Contract.Status.WITHDRAWN,
                timestamp_field="withdrawn_at",
            )
        return contract

    if any_active:
        if contract.status != Contract.Status.IN_PROGRESS:
            _set_contract_state(
                contract,
                Contract.Status.IN_PROGRESS,
                timestamp_field="active_at",
            )
        return contract

    return contract


@transaction.atomic
def resolve_dispute_to_freelancer(*, milestone: Milestone, user, note: str = "") -> Milestone:
    """
    Admin-only resolution path:
    release the disputed escrow to the freelancer.
    """
    if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
        raise PermissionDenied(_("Only staff can resolve disputes."))

    milestone = Milestone.objects.select_for_update().select_related(
        "contract",
        "contract__freelancer__account__user",
    ).get(pk=milestone.pk)

    if milestone.status != Milestone.Status.DISPUTED:
        raise ValidationError({"status": _("Only disputed milestones can be resolved.")})

    hold = _get_milestone_hold(milestone)
    if not hold:
        raise EscrowHoldError(_("Escrow hold not found for this milestone."))

    recipient_wallet = get_or_create_wallet_for_user(
        milestone.contract.freelancer.account.user,
        currency=milestone.contract.currency or PAYMENTS_DEFAULT_CURRENCY,
    )

    release_escrow_hold_to_wallet(
        hold=hold,
        recipient_wallet=recipient_wallet,
        idempotency_key=f"milestone:{milestone.public_id}:dispute:release",
        initiated_by=user,
        amount=milestone.amount,
        reference_type="milestone",
        reference_id=str(milestone.public_id),
        description=note or _("Dispute resolved in favor of the freelancer."),
        metadata={
            "contract_public_id": milestone.contract.public_id,
            "milestone_public_id": milestone.public_id,
            "resolution": "freelancer",
        },
    )

    milestone.status = Milestone.Status.RELEASED
    milestone.resolution_note = note or milestone.resolution_note
    milestone.released_at = timezone.now()
    milestone.full_clean()
    milestone.save()

    sync_contract_completion(milestone.contract)

    log_contract_event(
        contract=milestone.contract,
        event_type=ContractEvent.ContractEventType.MILESTONE_DISPUTE_RESOLVED_TO_FREELANCER,
        actor=user,
        metadata={"milestone_public_id": milestone.public_id, "note": note},
    )

    return milestone


@transaction.atomic
def resolve_dispute_to_client(*, milestone: Milestone, user, note: str = "") -> Milestone:
    """
    Admin-only resolution path:
    refund the disputed escrow back to the client wallet.
    """
    if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
        raise PermissionDenied(_("Only staff can resolve disputes."))

    milestone = Milestone.objects.select_for_update().select_related(
        "contract",
        "contract__client__account__user",
    ).get(pk=milestone.pk)

    if milestone.status != Milestone.Status.DISPUTED:
        raise ValidationError({"status": _("Only disputed milestones can be resolved.")})

    hold = _get_milestone_hold(milestone)
    if not hold:
        raise EscrowHoldError(_("Escrow hold not found for this milestone."))

    refund_escrow_hold(
        hold=hold,
        idempotency_key=f"milestone:{milestone.public_id}:dispute:refund",
        initiated_by=user,
        amount=milestone.amount,
        reference_type="milestone",
        reference_id=str(milestone.public_id),
        description=note or _("Dispute resolved in favor of the client."),
        metadata={
            "contract_public_id": milestone.contract.public_id,
            "milestone_public_id": milestone.public_id,
            "resolution": "client",
        },
    )

    milestone.status = Milestone.Status.REFUNDED
    milestone.resolution_note = note or milestone.resolution_note
    milestone.refunded_at = timezone.now()
    milestone.full_clean()
    milestone.save()

    sync_contract_completion(milestone.contract)

    log_contract_event(
        contract=milestone.contract,
        event_type=ContractEvent.ContractEventType.MILESTONE_DISPUTE_RESOLVED_TO_CLIENT,
        actor=user,
        metadata={"milestone_public_id": milestone.public_id, "note": note},
    )

    return milestone

def _sum_plan_items(items):
    return sum((item.amount for item in items), Decimal("0.00"))


@transaction.atomic
def validate_milestone_plan(plan: MilestonePlan):
    items = list(plan.items.order_by("order", "created_at"))
    if not items:
        raise ValidationError({"detail": "Milestone plan must have at least one item."})

    if len(items) > MAX_MILESTONES:
        raise ValidationError({"detail": f"Maximum {MAX_MILESTONES} milestones allowed."})

    total = _sum_plan_items(items)

    if plan.proposal.job.milestone_mode == "SINGLE" and len(items) != 1:
        raise ValidationError({"detail": "Single mode allows exactly one milestone."})

    if len(items) > 1:
        first_cap = (plan.proposal.job.budget_total or total) * MAX_FIRST_MILESTONE_PERCENT / Decimal("100")
        last_floor = (plan.proposal.job.budget_total or total) * MIN_LAST_MILESTONE_PERCENT / Decimal("100")

        if items[0].amount > first_cap:
            raise ValidationError({"detail": "First milestone is too large."})
        if items[-1].amount < last_floor:
            raise ValidationError({"detail": "Last milestone is too small."})

    if plan.proposal.job.pricing_mode == "FIXED" and plan.proposal.job.budget_total is not None:
        if total != plan.proposal.job.budget_total:
            raise ValidationError({"detail": "Milestones must equal the fixed budget total."})

    plan.total_amount = total
    plan.save(update_fields=["total_amount", "updated_at"])
    return total


@transaction.atomic
def approve_milestone_plan(plan: MilestonePlan):
    validate_milestone_plan(plan)
    plan.status = MilestonePlan.Status.APPROVED
    plan.save(update_fields=["status", "updated_at"])
    return plan


@transaction.atomic
def create_contract_from_accepted_proposal(proposal, created_by):
    """
    Turn the accepted bid + approved milestone plan into a live contract.
    """
    plan = proposal.milestone_plans.filter(status=MilestonePlan.Status.APPROVED).order_by("created_at").first()
    if not plan:
        raise ValidationError({"detail": "No approved milestone plan found."})

    total = validate_milestone_plan(plan)

    contract = Contract.objects.create(
        proposal=proposal,
        client=proposal.job.client_profile,
        freelancer=proposal.freelancer,
        title=proposal.job.title,
        status=Contract.Status.PENDING_APPROVAL,
        milestone_mode=proposal.job.milestone_mode,
        split_owner=proposal.job.split_owner,
        collab_allowed=proposal.job.collab_allowed,
        budget_total=proposal.job.budget_total or total,
        agreed_price=total,
    )

    for item in plan.items.order_by("order", "created_at"):
        milestone = Milestone.objects.create(
            contract=contract,
            source_item=item,
            title=item.title,
            description=item.description,
            amount=item.amount,
            due_date=item.due_date,
            order=item.order,
            status=Milestone.Status.PENDING,
        )
        item.status = MilestonePlanItem.Status.CONVERTED
        item.save(update_fields=["status", "updated_at"])

    contract.status = Contract.Status.ACTIVE
    contract.active_at = timezone.now()
    contract.save(update_fields=["status", "active_at", "updated_at"])
    return contract


# @transaction.atomic
# def submit_milestone_work(*, milestone: Milestone, user, note: str = "", external_link: str = "", payload=None):
#     submission = MilestoneSubmission.objects.create(
#         milestone=milestone,
#         submitted_by=user,
#         note=note,
#         external_link=external_link,
#         payload=payload or {},
#     )
#     milestone.status = Milestone.Status.SUBMITTED
#     milestone.submitted_at = timezone.now()
#     milestone.save(update_fields=["status", "submitted_at", "updated_at"])
#     return submission

