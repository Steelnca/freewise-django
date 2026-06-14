"""
Freewise contract views.

These views do not mutate models directly.
They delegate all state changes to contracts/services.py.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone
from django.db import transaction

from proposals.models import Proposal

from .models import Contract, Milestone, MilestonePlan, MilestonePlanItem, MilestoneSubmission
from .serializers import ContractSerializer, MilestoneActionSerializer, MilestoneSerializer, MilestoneSubmissionSerializer, MilestonePlanSerializer, MilestonePlanItemSerializer, ContractEventSerializer
from .services import (
    approve_milestone,
    cancel_contract,
    ensure_party_access,
    open_dispute,
    request_revision,
    ensure_milestone_access,
    approve_milestone_plan,
    submit_milestone,
)
from .constants import MAX_FIRST_MILESTONE_PERCENT, MAX_MILESTONES, MIN_LAST_MILESTONE_PERCENT


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _payload(request) -> Dict[str, Any]:
    if isinstance(request.data, dict):
        return request.data
    return dict(request.data or {})


def _to_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _user_client_profile(user):
    account = getattr(user, "account", None)
    return getattr(account, "client_profile", None)


def _user_freelancer_profile(user):
    account = getattr(user, "account", None)
    return getattr(account, "freelancer_profile", None)


def _job_client_profile(job):
    for attr in ("client", "client_profile", "owner", "client_user"):
        value = getattr(job, attr, None)
        if value is not None:
            return value
    return None


def _job_currency(job) -> str:
    for attr in ("currency", "default_currency"):
        value = getattr(job, attr, None)
        if value:
            return str(value)
    return "DZD"


def _ensure_contract_party_access(contract: Contract, user) -> None:
    client = _user_client_profile(user)
    freelancer = _user_freelancer_profile(user)

    if client and contract.client_id == client.id:
        return
    if freelancer and contract.freelancer_id == freelancer.id:
        return

    raise PermissionDenied(_("You are not allowed to access this contract."))


def _ensure_proposal_party_access(proposal: Proposal, user) -> None:
    client = _user_client_profile(user)
    freelancer = _user_freelancer_profile(user)
    job_client = _job_client_profile(proposal.job)

    if client and job_client and getattr(job_client, "id", None) == client.id:
        return
    if freelancer and proposal.freelancer_id == freelancer.id:
        return

    raise PermissionDenied(_("You are not allowed to access this proposal."))


def _item_source_for_user(user) -> str:
    if _user_client_profile(user):
        return MilestonePlanItem.Source.CLIENT
    return MilestonePlanItem.Source.FREELANCER


def _sum_items(items: List[MilestonePlanItem]) -> Decimal:
    total = Decimal("0.00")
    for item in items:
        total += item.amount
    return total


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
                "amount": _to_decimal(amount_raw),
                "due_date": due_date,
                "order": int(raw.get("order") or index),
                "source": str(raw.get("source") or "").upper().strip(),
                "can_be_suggested": bool(raw.get("can_be_suggested", True)),
                "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
            }
        )

    return items


def _replace_plan_items(plan: MilestonePlan, items_payload: List[Dict[str, Any]]) -> List[MilestonePlanItem]:
    plan.items.all().delete()

    created: List[MilestonePlanItem] = []
    for item_data in items_payload:
        source = item_data["source"] or _item_source_for_user(plan.created_by)
        if source not in MilestonePlanItem.Source.values:
            source = _item_source_for_user(plan.created_by)

        item = MilestonePlanItem.objects.create(
            plan=plan,
            title=item_data["title"],
            description=item_data["description"],
            amount=item_data["amount"],
            due_date=item_data["due_date"],
            order=item_data["order"],
            source=source,
            status=MilestonePlanItem.Status.DRAFT,
            can_be_suggested=item_data["can_be_suggested"],
            metadata=item_data["metadata"],
        )
        created.append(item)

    return created


def _validate_plan_rules(plan: MilestonePlan) -> Decimal:
    items = list(plan.items.order_by("order", "created_at"))
    total = _sum_items(items)

    if not items:
        raise ValidationError({"detail": _("Milestone plan must contain at least one item.")})

    if len(items) > MAX_MILESTONES:
        raise ValidationError({"detail": _(f"Maximum {MAX_MILESTONES} milestones are allowed.")})

    job = plan.proposal.job
    milestone_mode = getattr(job, "milestone_mode", "SINGLE")
    pricing_mode = getattr(job, "pricing_mode", "NEGOTIABLE")
    budget_total = getattr(job, "budget_total", None)

    if milestone_mode == "SINGLE" and len(items) != 1:
        raise ValidationError({"detail": _("Single milestone mode allows exactly one item.")})

    if len(items) > 1:
        basis = budget_total or total
        first_cap = basis * MAX_FIRST_MILESTONE_PERCENT / Decimal("100")
        last_floor = basis * MIN_LAST_MILESTONE_PERCENT / Decimal("100")

        if items[0].amount > first_cap:
            raise ValidationError({"detail": _("The first milestone is too large.")})
        if items[-1].amount < last_floor:
            raise ValidationError({"detail": _("The last milestone is too small.")})

    if pricing_mode == "FIXED" and budget_total is not None:
        if total != budget_total:
            raise ValidationError({"detail": _("Milestones must equal the fixed budget total.")})

    plan.total_amount = total
    plan.save(update_fields=["total_amount", "updated_at"])
    return total


def _get_or_create_active_plan(proposal: Proposal, created_by) -> MilestonePlan:
    active_plan = (
        proposal.milestone_plans.filter(
            status__in=[MilestonePlan.Status.DRAFT, MilestonePlan.Status.PROPOSED]
        )
        .order_by("-created_at")
        .first()
    )

    if active_plan:
        return active_plan

    return MilestonePlan.objects.create(
        proposal=proposal,
        created_by=created_by,
        status=MilestonePlan.Status.DRAFT,
        suggestion_enabled=True,
        currency=_job_currency(proposal.job),
    )


def _create_contract_from_approved_plan(proposal: Proposal, plan: MilestonePlan, created_by) -> Contract:
    total = _validate_plan_rules(plan)
    job = proposal.job
    client = _job_client_profile(job)

    if client is None:
        raise ValidationError({"detail": _("Job client profile could not be resolved.")})

    contract = Contract.objects.create(
        proposal=proposal,
        client=client,
        freelancer=proposal.freelancer,
        title=getattr(job, "title", "") or getattr(proposal, "title", "") or _("Contract"),
        notes=getattr(proposal, "cover_letter", "") or "",
        status=Contract.Status.ACTIVE,
        milestone_mode=getattr(job, "milestone_mode", Contract.MilestoneMode.SINGLE),
        split_owner=getattr(job, "split_owner", Contract.SplitOwner.CLIENT),
        collab_allowed=getattr(job, "collab_allowed", False),
        budget_total=getattr(job, "budget_total", None) or total,
        agreed_price=total,
        active_at=timezone.now(),
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

    return contract


# -----------------------------------------------------------------------------
# Contract list/detail
# -----------------------------------------------------------------------------
class ContractListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        contracts = (
            Contract.objects.select_related("proposal", "client", "freelancer")
            .prefetch_related("milestones")
            .order_by("-created_at")
        )

        client = _user_client_profile(request.user)
        freelancer = _user_freelancer_profile(request.user)

        if client and freelancer:
            contracts = contracts.filter(client=client) | contracts.filter(freelancer=freelancer)
        elif client:
            contracts = contracts.filter(client=client)
        elif freelancer:
            contracts = contracts.filter(freelancer=freelancer)
        else:
            contracts = Contract.objects.none()

        return Response(ContractSerializer(contracts.distinct(), many=True).data, status=status.HTTP_200_OK)


class ContractDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, public_id: str):
        contract = get_object_or_404(
            Contract.objects.select_related("proposal", "client", "freelancer")
            .prefetch_related("milestones", "milestones__submissions"),
            public_id=public_id,
        )
        _ensure_contract_party_access(contract, request.user)
        return Response(ContractSerializer(contract).data, status=status.HTTP_200_OK)

# -----------------------------------------------------------------------------
# Milestone plan endpoints
# -----------------------------------------------------------------------------
class MilestonePlanCreateView(APIView):
    """
    POST body:
    {
      "proposal_public_id": "...",
      "note": "...",
      "suggestion_enabled": true,
      "items": [
        {
          "title": "...",
          "description": "...",
          "amount": "100.00",
          "due_date": "2026-01-01",
          "order": 1,
          "source": "CLIENT|FREELANCER",
          "can_be_suggested": true,
          "metadata": {}
        }
      ]
    }
    """
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        payload = _payload(request)
        proposal_public_id = str(payload.get("proposal_public_id") or "").strip()
        if not proposal_public_id:
            raise ValidationError({"proposal_public_id": _("Proposal public id is required.")})

        proposal = get_object_or_404(
            Proposal.objects.select_related("job", "freelancer"),
            public_id=proposal_public_id,
        )
        _ensure_proposal_party_access(proposal, request.user)

        items_payload = _plan_items_payload(payload.get("items"))
        plan = _get_or_create_active_plan(proposal, created_by=request.user)

        if plan.status in {MilestonePlan.Status.APPROVED, MilestonePlan.Status.LOCKED}:
            raise ValidationError({"detail": _("Approved plans cannot be edited.")})

        plan.note = str(payload.get("note") or "").strip()
        plan.suggestion_enabled = bool(payload.get("suggestion_enabled", True))
        plan.status = MilestonePlan.Status.PROPOSED if plan.note or items_payload else MilestonePlan.Status.DRAFT
        plan.currency = _job_currency(proposal.job)
        plan.save(update_fields=["note", "suggestion_enabled", "status", "currency", "updated_at"])

        created_items = _replace_plan_items(plan, items_payload)
        _validate_plan_rules(plan)

        serializer = MilestonePlanSerializer(plan)
        return Response(
            {
                "plan": serializer.data,
                "items": MilestonePlanItemSerializer(created_items, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MilestonePlanDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, public_id: str) -> MilestonePlan:
        return get_object_or_404(
            MilestonePlan.objects.select_related("proposal", "created_by", "proposal__job")
            .prefetch_related("items"),
            public_id=public_id,
        )

    def get(self, request, public_id: str):
        plan = self.get_object(public_id)
        _ensure_proposal_party_access(plan.proposal, request.user)
        return Response(MilestonePlanSerializer(plan).data, status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request, public_id: str):
        plan = self.get_object(public_id)
        _ensure_proposal_party_access(plan.proposal, request.user)

        if plan.status in {MilestonePlan.Status.APPROVED, MilestonePlan.Status.LOCKED}:
            raise ValidationError({"detail": _("Approved plans cannot be edited.")})

        payload = _payload(request)
        if "note" in payload:
            plan.note = str(payload.get("note") or "").strip()
        if "suggestion_enabled" in payload:
            plan.suggestion_enabled = bool(payload.get("suggestion_enabled"))
        if "items" in payload:
            items_payload = _plan_items_payload(payload.get("items"))
            _replace_plan_items(plan, items_payload)

        if payload.get("status") in MilestonePlan.Status.values:
            plan.status = payload.get("status")

        plan.save(update_fields=["note", "suggestion_enabled", "status", "updated_at"])
        _validate_plan_rules(plan)

        return Response(MilestonePlanSerializer(plan).data, status=status.HTTP_200_OK)

    @transaction.atomic
    def delete(self, request, public_id: str):
        plan = self.get_object(public_id)
        _ensure_proposal_party_access(plan.proposal, request.user)

        if plan.status in {MilestonePlan.Status.APPROVED, MilestonePlan.Status.LOCKED}:
            raise ValidationError({"detail": _("Approved plans cannot be deleted.")})

        plan.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MilestonePlanApproveView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, public_id: str):
        plan = get_object_or_404(
            MilestonePlan.objects.select_related("proposal", "created_by", "proposal__job")
            .prefetch_related("items"),
            public_id=public_id,
        )
        _ensure_proposal_party_access(plan.proposal, request.user)

        approve_milestone_plan(plan)

        proposal = plan.proposal
        contract = getattr(proposal, "contract", None)

        if proposal.status == Proposal.Status.ACCEPTED:
            if contract is None:
                contract = _create_contract_from_approved_plan(proposal, plan, request.user)
                return Response(ContractSerializer(contract).data, status=status.HTTP_201_CREATED)

            # Contract already exists: ensure agreed price mirrors the approved plan.
            total = _validate_plan_rules(plan)
            contract.agreed_price = total
            if not contract.budget_total:
                contract.budget_total = total
            contract.save(update_fields=["agreed_price", "budget_total", "updated_at"])
            return Response(ContractSerializer(contract).data, status=status.HTTP_200_OK)

        return Response(MilestonePlanSerializer(plan).data, status=status.HTTP_200_OK)


# -----------------------------------------------------------------------------
# Milestone submission endpoint
# -----------------------------------------------------------------------------
class MilestoneSubmissionCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, public_id: str):
        milestone = get_object_or_404(
            Milestone.objects.select_related("contract", "contract__freelancer", "contract__client"),
            public_id=public_id,
        )

        try:
            ensure_party_access(milestone.contract, request.user)
            serializer = MilestoneActionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            payload = _payload(request)
            submission_payload = payload.get("payload")
            if submission_payload is not None and not isinstance(submission_payload, dict):
                raise ValidationError({"payload": _("Submission payload must be an object.")})

            submission = submit_milestone(
                milestone=milestone,
                user=request.user,
                submission_note=serializer.validated_data.get("note", ""),
                submission_link=serializer.validated_data.get("submission_link", ""),
                payload=payload,
            )

        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            MilestoneSubmissionSerializer(submission).data,
            status=status.HTTP_201_CREATED,
        )


class RequestRevisionView(APIView):
    """
    POST /api/contracts/milestones/<pk>/request-revision/
    Client asks for a revision.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, public_id):
        milestone = get_object_or_404(Milestone.objects.select_related("contract"), public_id=public_id)

        try:
            ensure_party_access(milestone.contract, request.user)
            serializer = MilestoneActionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            updated = request_revision(
                milestone=milestone,
                user=request.user,
                revision_note=serializer.validated_data.get("revision_note", ""),
            )
        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(MilestoneSerializer(updated).data, status=status.HTTP_200_OK)

class ApproveMilestoneView(APIView):
    """
    POST /api/contracts/milestones/<public_id>/approve/
    Client approves milestone and triggers escrow release.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, public_id):
        milestone = get_object_or_404(Milestone.objects.select_related("contract"), public_id=public_id)

        try:
            ensure_party_access(milestone.contract, request.user)
            serializer = MilestoneActionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            updated = approve_milestone(
                milestone=milestone,
                user=request.user,
                review_note=serializer.validated_data.get("review_note", ""),
            )
        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(MilestoneSerializer(updated).data, status=status.HTTP_200_OK)

class DisputeMilestoneView(APIView):
    """
    POST /api/contracts/milestones/<pk>/dispute/
    Client opens a dispute.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, public_id):
        milestone = get_object_or_404(Milestone.objects.select_related("contract"), public_id=public_id)

        try:
            ensure_party_access(milestone.contract, request.user)
            serializer = MilestoneActionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            updated = open_dispute(
                milestone=milestone,
                user=request.user,
                dispute_reason=serializer.validated_data.get("reason", ""),
            )
        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(MilestoneSerializer(updated).data, status=status.HTTP_200_OK)

class CancelContractView(APIView):
    """
    POST /api/contracts/<pk>/cancel/
    Either party can cancel while the contract is not completed.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, public_id):
        contract = get_object_or_404(Contract, public_id=public_id)

        try:
            ensure_party_access(contract, request.user)
            reason = (request.data.get("reason") or "").strip()
            updated = cancel_contract(contract=contract, user=request.user, reason=reason)
        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(ContractSerializer(updated).data, status=status.HTTP_200_OK)

class ContractEventsView(generics.ListAPIView):
    serializer_class = ContractEventSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    def get_queryset(self):
        contract = get_object_or_404(Contract, public_id=self.kwargs["public_id"])
        ensure_party_access(contract, self.request.user)
        return contract.events.order_by("-created_at")

class MilestoneDeliverableRedirectView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, public_id):
        milestone = get_object_or_404(
            Milestone.objects.select_related("contract"),
            public_id=public_id,
        )
        ensure_milestone_access(milestone, request.user)

        if not milestone.submission_link:
            return Response(
                {"detail": "No deliverable link found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return redirect(milestone.submission_link)