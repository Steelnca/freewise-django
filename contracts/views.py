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

from jobs.models import Job
from proposals.models import Proposal

from .models import Contract, Milestone, MilestonePlan, MilestonePlanItem, MilestoneSubmission
from .serializers import ContractSerializer, MilestoneActionSerializer, MilestoneSerializer, MilestoneSubmissionSerializer, MilestonePlanSerializer, MilestonePlanItemSerializer, ContractEventSerializer
from .services import (
    cancel_contract,
    create_contract_from_selected_plan,
    ensure_party_access,
    open_dispute,
    request_revision,
    approve_milestone_plan,
    submit_milestone,
    _payload,
    ensure_milestone_access,
    _user_client_profile,
    _user_freelancer_profile,
    approve_milestone,
    _job_currency,
    _ensure_contract_party_access,
    _ensure_job_party_access,
    _ensure_proposal_party_access,
    _plan_items_payload,
    _replace_plan_items,
    _validate_plan_rules,
    _get_or_create_active_plan,
)


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

class MilestonePlanCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, proposal_public_id):
        payload = _payload(request)

        job_public_id = str(payload.get("job_public_id") or "").strip()
        if not job_public_id:
            raise ValidationError({"job_public_id": _("Job public id is required.")})

        job = get_object_or_404(
            Job.objects.select_related("client__account__user", "category"),
            public_id=job_public_id,
        )

        proposal = None
        if proposal_public_id:
            proposal = get_object_or_404(
                Proposal.objects.select_related("job", "freelancer"),
                public_id=proposal_public_id,
            )
            if proposal.job_id != job.id:
                raise ValidationError({"proposal_public_id": _("Proposal does not belong to this job.")})

        role = _ensure_job_party_access(job, request.user, proposal=proposal)

        if proposal is None and role != "client":
            raise PermissionDenied(_("Only the client can create a job-level plan."))

        if proposal is not None and role == "client":
            if proposal.status != Proposal.Status.SHORTLISTED:
                raise ValidationError(
                    {"proposal_public_id": _("Client-created proposal plans require a shortlisted proposal.")}
                )

        if proposal is not None and role == "freelancer":
            freelancer = _user_freelancer_profile(request.user)
            if not freelancer or proposal.freelancer_id != freelancer.id:
                raise PermissionDenied(_("Only the proposal owner can create this plan."))

        items_payload = _plan_items_payload(payload.get("items"))
        if not items_payload:
            raise ValidationError({"items": _("Milestone plan must contain at least one item.")})

        plan = _get_or_create_active_plan(
            job=job,
            proposal=proposal,
            created_by=request.user,
            source_role=MilestonePlan.SourceRole.CLIENT if role == "client" else MilestonePlan.SourceRole.FREELANCER,
        )

        if plan.status in {MilestonePlan.Status.APPROVED, MilestonePlan.Status.CONVERTED}:
            raise ValidationError({"detail": _("Approved plans cannot be edited.")})

        plan.note = str(payload.get("note") or "").strip()
        plan.suggestion_enabled = bool(payload.get("suggestion_enabled", True))
        plan.currency = _job_currency(job)
        plan.source_role = MilestonePlan.SourceRole.CLIENT if role == "client" else MilestonePlan.SourceRole.FREELANCER
        plan.save(update_fields=["note", "suggestion_enabled", "currency", "source_role", "updated_at"])

        created_items = _replace_plan_items(plan, items_payload)
        _validate_plan_rules(plan)

        # Client-created plans are ready immediately.
        if role == "client":
            MilestonePlan.objects.filter(job=job, is_selected=True).exclude(pk=plan.pk).update(
                is_selected=False,
                selected_at=None,
            )
            plan.status = MilestonePlan.Status.APPROVED
            plan.is_selected = True
            update_fields = ["status", "is_selected", "updated_at"]

            if hasattr(plan, "selected_at"):
                plan.selected_at = timezone.now()
                update_fields.insert(2, "selected_at")

            plan.save(update_fields=update_fields)

            shortlisted = (
                proposal if proposal is not None and proposal.status == Proposal.Status.SHORTLISTED
                else Proposal.objects.filter(
                    job=job,
                    status=Proposal.Status.SHORTLISTED,
                )
                .order_by("-created_at")
                .first()
            )

            if shortlisted:
                contract = create_contract_from_selected_plan(
                    job=job,
                    plan=plan,
                    proposal=shortlisted,
                    created_by=request.user,
                )
                shortlisted.status = Proposal.Status.CONTRACTED
                if hasattr(shortlisted, "contracted_at"):
                    shortlisted.contracted_at = timezone.now()
                    shortlisted.save(update_fields=["status", "contracted_at", "updated_at"])
                else:
                    shortlisted.save(update_fields=["status", "updated_at"])

                Proposal.objects.filter(job=job).exclude(pk=shortlisted.pk).update(
                    status=Proposal.Status.REJECTED
                )

                job.status = Job.Status.IN_PROGRESS
                job.save(update_fields=["status"])

                return Response(ContractSerializer(contract).data, status=status.HTTP_201_CREATED)

            return Response(
                {
                    "plan": MilestonePlanSerializer(plan).data,
                    "items": MilestonePlanItemSerializer(created_items, many=True).data,
                },
                status=status.HTTP_201_CREATED,
            )

        # Freelancer-created plans stay as draft/proposed until the client approves them.
        plan.status = MilestonePlan.Status.PROPOSED if (plan.note or items_payload) else MilestonePlan.Status.DRAFT
        plan.save(update_fields=["status", "updated_at"])

        return Response(
            {
                "plan": MilestonePlanSerializer(plan).data,
                "items": MilestonePlanItemSerializer(created_items, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )

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
                submission_note=(
                    serializer.validated_data.get("submission_note")
                    or serializer.validated_data.get("note", "")
                ),
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
                revision_scope=serializer.validated_data.get("revision_scope", ""),
            )
        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(MilestoneSerializer(updated).data, status=status.HTTP_200_OK)

class MilestonePlanApproveView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, public_id: str):
        plan = get_object_or_404(
            MilestonePlan.objects.select_related("job", "proposal", "created_by")
            .prefetch_related("items"),
            public_id=public_id,
        )
        _ensure_job_party_access(plan.job, request.user, proposal=plan.proposal)
        client = _user_client_profile(request.user)
        if not client or plan.job.client_id != client.id:
            raise PermissionDenied(_("Only the client can approve milestone plans."))

        if plan.status == MilestonePlan.Status.CONVERTED:
            raise ValidationError({"detail": _("Converted plans cannot be approved again.")})

        if plan.status != MilestonePlan.Status.APPROVED:
            approve_milestone_plan(plan)

        MilestonePlan.objects.filter(job=plan.job).exclude(pk=plan.pk).update(is_selected=False)

        if not plan.is_selected:
            plan.is_selected = True
            update_fields = ["is_selected", "updated_at"]
            if hasattr(plan, "selected_at"):
                plan.selected_at = timezone.now()
                update_fields.insert(1, "selected_at")
            plan.save(update_fields=update_fields)

        shortlisted = plan.proposal or (
            Proposal.objects.filter(
                job=plan.job,
                status=Proposal.Status.SHORTLISTED,
            )
            .order_by("-created_at")
            .first()
        )

        if not shortlisted or shortlisted.status != Proposal.Status.SHORTLISTED:
            return Response(
                {
                    "detail": _("Milestone plan approved and selected. Waiting for shortlisted proposal."),
                    "plan": MilestonePlanSerializer(plan).data,
                },
                status=status.HTTP_200_OK,
            )

        contract = getattr(shortlisted, "contract", None)

        if contract is None:
            contract = create_contract_from_selected_plan(
                job=plan.job,
                plan=plan,
                proposal=shortlisted,
                created_by=request.user,
            )
            shortlisted.status = Proposal.Status.CONTRACTED
            if hasattr(shortlisted, "contracted_at"):
                shortlisted.contracted_at = timezone.now()
                shortlisted.save(update_fields=["status", "contracted_at", "updated_at"])
            else:
                shortlisted.save(update_fields=["status", "updated_at"])

            Proposal.objects.filter(job=plan.job).exclude(pk=shortlisted.pk).update(
                status=Proposal.Status.REJECTED
            )

            plan.job.status = Job.Status.IN_PROGRESS
            plan.job.save(update_fields=["status"])

            return Response(ContractSerializer(contract).data, status=status.HTTP_201_CREATED)

        total = _validate_plan_rules(plan)
        contract.agreed_price = total

        update_fields = ["agreed_price", "updated_at"]

        if hasattr(contract, "budget_total") and not contract.budget_total:
            contract.budget_total = total
            update_fields.append("budget_total")

        if hasattr(contract, "source_plan"):
            contract.source_plan = plan
            update_fields.append("source_plan")

        contract.save(update_fields=update_fields)
        return Response(ContractSerializer(contract).data, status=status.HTTP_200_OK)

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

class MilestonePlanDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, public_id: str) -> MilestonePlan:
        return get_object_or_404(
            MilestonePlan.objects.select_related("job", "proposal", "created_by", "job__client__account__user")
            .prefetch_related("items"),
            public_id=public_id,
        )

    def get(self, request, public_id: str):
        plan = self.get_object(public_id)
        _ensure_job_party_access(plan.job, request.user, proposal=plan.proposal)
        return Response(MilestonePlanSerializer(plan).data, status=status.HTTP_200_OK)

    def patch(self, request, public_id: str):
        plan = self.get_object(public_id)
        _ensure_job_party_access(plan.job, request.user, proposal=plan.proposal)
        return super().patch(self, request, public_id)

    def delete(self, request, public_id: str):
        plan = self.get_object(public_id)
        _ensure_job_party_access(plan.job, request.user, proposal=plan.proposal)
        return super().delete(self, request, public_id)

    @transaction.atomic
    def patch(self, request, public_id: str):
        plan = self.get_object(public_id)
        _ensure_job_party_access(plan.job, request.user, proposal=plan.proposal)

        if plan.status in {MilestonePlan.Status.APPROVED, MilestonePlan.Status.CONVERTED}:
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
        _ensure_job_party_access(plan.job, request.user, proposal=plan.proposal)

        if plan.status in {MilestonePlan.Status.APPROVED, MilestonePlan.Status.CONVERTED}:
            raise ValidationError({"detail": _("Approved plans cannot be deleted.")})

        plan.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

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
