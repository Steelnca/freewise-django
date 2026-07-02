from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone
from django.db.models import QuerySet


from jobs.models import Job
from contracts.models import Contract, ContractEvent
from contracts.services import (
    log_contract_event,
    ensure_contract_field_exists,
    sync_contract_completion,
    set_contract_state,
    contract_reference_for_milestone
)

from payments.models import EscrowHold
from payments.constants import DEFAULT_CURRENCY as PAYMENTS_DEFAULT_CURRENCY
from payments.services import (
    EscrowHoldError,
    calculate_platform_fee,
    get_or_create_platform_wallet,
    get_or_create_wallet_for_user,
    release_escrow_hold_to_wallet,
    milestone_has_settled_or_paid_payment,
    refund_escrow_hold,
    normalize_money,
)
from core.access import job_client_profile, ensure_milestone_access
from core.utils import to_decimal

from .models import Milestone, MilestonePlan, MilestonePlanItem, MilestoneSubmission, MilestoneTemplate
from .constants import MAX_FIRST_MILESTONE_PERCENT, MIN_LAST_MILESTONE_PERCENT, MAX_MILESTONES


def job_currency(job) -> str:
    for attr in ("currency", "default_currency"):
        value = getattr(job, attr, None)
        if value:
            return str(value)
    return "DZD"


def get_plan_mode(plan: MilestonePlan) -> str:
    for attr in ("mode", "milestone_mode"):
        value = getattr(plan, attr, None)
        if value:
            return str(value).upper()
    raise ValidationError({"detail": _("Milestone plan mode is missing.")})


def _sum_items(items: List[MilestonePlanItem]) -> Decimal:
    total = Decimal("0.00")
    for item in items:
        total += item.amount
    return total

def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _plan_items_payload(items_raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(items_raw, list):
        raise ValidationError({"items": _("Milestone items must be a list.")})

    items: List[Dict[str, Any]] = []
    for index, raw in enumerate(items_raw, start=1):
        if not isinstance(raw, dict):
            raise ValidationError({"items": _("Each milestone item must be an object.")})

        title = str(raw.get("title") or "").strip()
        amount_raw = raw.get("amount")
        due_date = raw.get("due_date")

        if not title:
            raise ValidationError({"items": _("Each milestone item must have a title.")})
        if amount_raw in (None, ""):
            raise ValidationError({"items": _("Each milestone item must have an amount.")})
        if not due_date:
            raise ValidationError({"items": _("Each milestone item must have a due date.")})

        items.append(
            {
                "title": title,
                "description": str(raw.get("description") or "").strip(),
                "amount": to_decimal(amount_raw),
                "due_date": due_date,
                "order": int(raw.get("order") or index),
                "currency": str(raw.get("currency") or "").strip(),
                "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
            }
        )

    return items


def _replace_plan_items(plan: MilestonePlan, items_payload: List[Dict[str, Any]]) -> List[MilestonePlanItem]:
    plan.items.all().delete()
    created: List[MilestonePlanItem] = []

    for item_data in items_payload:
        item = MilestonePlanItem.objects.create(
            plan=plan,
            title=item_data["title"],
            description=item_data["description"],
            currency=item_data["currency"] or plan.currency,
            amount=item_data["amount"],
            due_date=item_data["due_date"],
            order=item_data["order"],
            status=MilestonePlanItem.Status.DRAFT,
            metadata=item_data["metadata"],
        )
        created.append(item)

    return created


def _validate_plan_rules(plan: MilestonePlan) -> Decimal:
    items = list(plan.items.order_by("order", "created_at"))
    total = _sum_items(items)

    if not items:
        raise ValidationError({"detail": _("Milestone plan must have at least one item.")})

    if len(items) > MAX_MILESTONES:
        raise ValidationError({"detail": _(f"Maximum {MAX_MILESTONES} milestones are allowed.")})

    mode = get_plan_mode(plan)
    pricing_mode = getattr(plan.job, "pricing_mode", None)
    budget_total = getattr(plan.job, "budget_total", None)

    if mode == "SINGLE" and len(items) != 1:
        raise ValidationError({"detail": _("Single milestone mode allows exactly one item.")})

    if pricing_mode == Job.PricingMode.FIXED:
        if budget_total is None:
            raise ValidationError({"detail": _("Fixed-price plans require a budget total.")})
        if total != budget_total:
            raise ValidationError({"detail": _("Milestones must equal the fixed budget total.")})

    if len(items) > 1:
        basis = budget_total if budget_total is not None else total
        first_cap = basis * MAX_FIRST_MILESTONE_PERCENT / Decimal("100")
        last_floor = basis * MIN_LAST_MILESTONE_PERCENT / Decimal("100")

        if items[0].amount > first_cap:
            raise ValidationError({"detail": _("The first milestone amount is too large.")})
        if items[-1].amount < last_floor:
            raise ValidationError({"detail": _("The last milestone amount is too small.")})

    plan.total_amount = total
    plan.save(update_fields=["total_amount", "updated_at"])
    return total

def _milestones_total(contract):
    return sum((m.amount for m in contract.milestones.all()), Decimal("0.00"))

def _get_milestone_hold(milestone: Milestone):
    """Look up the escrow hold tied to a milestone contract reference."""
    return (
        EscrowHold.objects.select_for_update()
        .filter(contract_reference=contract_reference_for_milestone(milestone))
        .first()
    )

def get_or_create_active_plan(*, job: Job, proposal, created_by, source_role: str) -> MilestonePlan:
    active_plan = (
        MilestonePlan.objects.filter(
            job=job,
            proposal=proposal,
            status__in=[MilestonePlan.Status.DRAFT, MilestonePlan.Status.PROPOSED],
        )
        .order_by("-created_at")
        .first()
    )

    if active_plan:
        return active_plan

    return MilestonePlan.objects.create(
        job=job,
        proposal=proposal,
        created_by=created_by,
        source_role=source_role,
        status=MilestonePlan.Status.DRAFT,
        mode=MilestonePlan.MilestoneMode.MULTI,
        suggestion_enabled=True,
        currency=job_currency(job),
    )



@transaction.atomic
def replace_plan_items_from_payload(plan: MilestonePlan, items_raw: Any) -> List[MilestonePlanItem]:
    items_payload = _plan_items_payload(items_raw)
    created = _replace_plan_items(plan, items_payload)
    _validate_plan_rules(plan)
    return created


@transaction.atomic
def validate_milestone_plan(plan: MilestonePlan) -> Decimal:
    return _validate_plan_rules(plan)


@transaction.atomic
def approve_milestone_plan(plan: MilestonePlan) -> MilestonePlan:
    validate_milestone_plan(plan)
    if plan.status != MilestonePlan.Status.APPROVED:
        plan.status = MilestonePlan.Status.APPROVED
        plan.save(update_fields=["status", "updated_at"])
    return plan


@transaction.atomic
def create_contract_from_selected_plan(*, job, plan: MilestonePlan, proposal, created_by=None) -> Contract:
    if plan.job_id != job.id:
        raise ValidationError({"detail": _("Milestone plan does not belong to this job.")})

    if plan.status != MilestonePlan.Status.APPROVED:
        raise ValidationError({"detail": _("Only approved plans can create a contract.")})

    if not plan.is_selected:
        raise ValidationError({"detail": _("Select a milestone plan before creating the contract.")})

    total = validate_milestone_plan(plan)
    client = job_client_profile(job)
    if client is None:
        raise ValidationError({"detail": _("Job client profile could not be resolved.")})

    if proposal is None:
        raise ValidationError({"detail": _("An shortlisted proposal is required.")})

    contract_kwargs = {
        "source_type": Contract.SourceType.JOB_BOARD,
        "job": job,
        "proposal": proposal,
        "client": client,
        "freelancer": proposal.freelancer,
        "title": getattr(job, "title", "") or _("Contract"),
        "notes": "",
        "status": Contract.Status.PENDING_FUNDING,
        "collab_allowed": getattr(job, "collab_allowed", False),
        "budget_total": getattr(job, "budget_total", None) or total,
        "agreed_price": total,
        "deadline": getattr(job, "deadline", None) or getattr(getattr(proposal, "job", None), "deadline", None),
        "currency": getattr(job, "currency", "") or getattr(proposal, "currency", "") or PAYMENTS_DEFAULT_CURRENCY,
    }
    if ensure_contract_field_exists("source_plan"):
        contract_kwargs["source_plan"] = plan

    contract = Contract.objects.create(**contract_kwargs)

    for item in plan.items.order_by("order", "created_at"):
        milestone = Milestone.objects.create(
            contract=contract,
            proposal=item,
            title=item.title,
            description=item.description,
            amount=item.amount,
            due_date=item.due_date,
            order=item.order,
            currency=item.currency or contract.currency,
            status=Milestone.Status.PENDING,
        )

        item.status = MilestonePlanItem.Status.CONVERTED
        item.save(update_fields=["status", "updated_at"])

    log_contract_event(
        contract=contract,
        event_type=ContractEvent.ContractEventType.CONTRACT_CREATED,
        actor=created_by,
        metadata={
            "job_public_id": job.public_id,
            "plan_public_id": plan.public_id,
            "proposal_public_id": getattr(proposal, "public_id", None),
        },
    )
    return contract

@transaction.atomic
def submit_milestone(
    *,
    milestone: Milestone,
    user,
    submission_note: str = "",
    submission_link: str = "",
    payload: dict = {},
) -> MilestoneSubmission:
    """Freelancer submits work for review."""
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
    """Client asks for changes on a submitted milestone."""
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
    """Client accepts delivered work.

    Escrow is released. Platform fee is deducted. Milestone becomes RELEASED.
    Contract may become COMPLETED.
    """
    ensure_milestone_access(milestone, user)

    account = getattr(user, "account", None)
    client = getattr(account, "client_profile", None)
    if not client or milestone.contract.client_id != client.id:
        raise PermissionDenied(_("Only the client can approve this milestone."))

    milestone = (
        Milestone.objects.select_for_update()
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
    """Open a dispute and freeze the milestone until moderation resolves it."""
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
    set_contract_state(
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
    """Refund a disputed or cancelled milestone back to the client wallet."""
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

def _in_range(value, minimum, maximum) -> bool:
    if value is None:
        return True
    if minimum is not None and value < minimum:
        return False
    if maximum is not None and value > maximum:
        return False
    return True


def score_template(job, template: MilestoneTemplate) -> int:
    score = 0

    # job kind
    job_kind = getattr(job, "job_kind", None) or getattr(job, "kind", None)
    if template.job_kind == MilestoneTemplate.JobKind.ANY:
        score += 10
    elif job_kind and template.job_kind == job_kind:
        score += 100

    # category
    job_category_id = getattr(job, "category_id", None)
    if template.category_id is None:
        score += 5
    elif job_category_id and template.category_id == job_category_id:
        score += 60

    # pricing mode
    pricing_mode = getattr(job, "pricing_mode", None)
    if template.pricing_mode == MilestoneTemplate.PricingMode.ANY:
        score += 5
    elif pricing_mode and template.pricing_mode == pricing_mode:
        score += 30

    # budget
    budget = getattr(job, "budget_total", None)
    if _in_range(budget, template.min_budget, template.max_budget):
        score += 25

    # duration / deadline days
    duration_days = getattr(job, "duration_days", None)
    if _in_range(duration_days, template.min_duration_days, template.max_duration_days):
        score += 15

    # step count preference
    step_count = template.steps.count()
    if template.min_steps <= step_count <= template.max_steps:
        score += 10

    # lower priority wins ties
    score -= int(template.priority or 0)

    return score


def pick_milestone_template(job) -> MilestoneTemplate | None:
    qs: QuerySet[MilestoneTemplate] = (
        MilestoneTemplate.objects.filter(is_active=True)
        .prefetch_related("steps")
    )

    if not qs.exists():
        return None

    ranked = sorted(qs, key=lambda t: score_template(job, t), reverse=True)
    best = ranked[0]

    # optional safety: if best score is too low, use a generic fallback
    if score_template(job, best) < 20:
        return qs.filter(job_kind=MilestoneTemplate.JobKind.ANY, category__isnull=True).order_by("priority", "created_at").first() or best

    return best



def build_plan_from_template(*, job, proposal, template: MilestoneTemplate, agreed_price: Decimal):
    steps = list(template.steps.order_by("order"))
    if not steps:
        raise ValueError("Template has no steps.")

    plan = MilestonePlan.objects.create(
        job=job,
        proposal=proposal,
        created_by=proposal.freelancer if proposal else job.client.user,
        source_role="FREELANCER" if proposal else "CLIENT",
        status=MilestonePlan.Status.DRAFT,
        total_amount=Decimal("0.00"),
        currency=getattr(job, "currency", "DZD"),
        suggestion_enabled=True,
    )

    total = Decimal("0.00")
    items = []

    for index, step in enumerate(steps, start=1):
        amount = _round_money((agreed_price * step.percent) / Decimal("100"))

        item = MilestonePlanItem.objects.create(
            plan=plan,
            title=step.title,
            description=step.outcome_summary,
            currency=plan.currency,
            amount=amount,
            due_date=None,  # fill later from job/proposal timing or user edit
            order=step.order,
            status=MilestonePlanItem.Status.DRAFT,
            metadata={
                "acceptance_criteria": step.acceptance_criteria,
                "deliverable_type": step.deliverable_type,
                "client_visible_note": step.client_visible_note,
                "internal_note": step.internal_note,
                "template_step_id": step.id,
                "is_final": step.is_final,
            },
        )
        items.append(item)
        total += amount

    # fix rounding remainder by adding it to the last item
    remainder = _round_money(agreed_price - total)
    if items and remainder != Decimal("0.00"):
        last = items[-1]
        last.amount = _round_money(last.amount + remainder)
        last.save(update_fields=["amount", "updated_at"])
        total = _round_money(total + remainder)

    plan.total_amount = total
    plan.save(update_fields=["total_amount", "updated_at"])

    return plan