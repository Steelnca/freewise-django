"""
Milestone API views.

Plan creation/editing lives here.
Contract creation happens only after explicit client approval.
Milestone execution calls service-layer functions only.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.access import (
    ensure_contract_party_access,
    ensure_job_party_access,
    user_client_profile,
    user_freelancer_profile,
)
from contracts.serializers import ContractSerializer
from contracts.services import (
    approve_milestone,
    create_contract_from_selected_plan,
    open_dispute,
    request_revision,
    submit_milestone,
)
from jobs.models import Job
from proposals.models import Proposal

from .models import Milestone, MilestonePlan
from .serializers import (
    MilestoneActionSerializer,
    MilestonePlanPatchSerializer,
    MilestonePlanSerializer,
    MilestonePlanWriteSerializer,
    MilestoneSerializer,
    MilestoneSubmissionSerializer,
)
from .services import (
    approve_milestone_plan,
    get_or_create_active_plan,
    job_currency,
    replace_plan_items_from_payload,
    validate_milestone_plan,
)

from .constants import EDITABLE_PLAN_STATUSES

class MilestonePlanCreateView(APIView):
    """
    POST /api/milestones/jobs/<job_public_id>/milestone-plans/

    proposal_public_id is optional in the body.
    The endpoint creates or replaces the active editable plan.
    It never approves the plan and never creates a contract.
    """

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, job_public_id: str):
        job = get_object_or_404(
            Job.objects.select_related("client__account__user", "category"),
            public_id=job_public_id,
        )

        serializer = MilestonePlanWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        proposal = None
        proposal_public_id = data.get("proposal_public_id")

        if proposal_public_id:
            proposal = get_object_or_404(
                Proposal.objects.select_related("job", "freelancer"),
                public_id=proposal_public_id,
            )

            if proposal.job_id != job.id:
                raise ValidationError(
                    {"proposal_public_id": _("This proposal does not belong to this job.")}
                )

        role = ensure_job_party_access(job, request.user, proposal=proposal)

        if proposal is None and role != "client":
            raise PermissionDenied(
                _("Only the client can create a job-level milestone plan.")
            )

        if role == "client" and proposal is not None:
            if proposal.status != Proposal.Status.SHORTLISTED:
                raise ValidationError(
                    {
                        "proposal_public_id": _(
                            "Choose a shortlisted proposal before creating a proposal plan."
                        )
                    }
                )

        if role == "freelancer":
            freelancer = user_freelancer_profile(request.user)
            if proposal is None or not freelancer or proposal.freelancer_id != freelancer.id:
                raise PermissionDenied(
                    _("Only the freelancer who owns this proposal can create its plan.")
                )

        plan = get_or_create_active_plan(
            job=job,
            proposal=proposal,
            created_by=request.user,
            source_role=(
                MilestonePlan.SourceRole.CLIENT
                if role == "client"
                else MilestonePlan.SourceRole.FREELANCER
            ),
        )

        if plan.status not in EDITABLE_PLAN_STATUSES:
            raise ValidationError(
                {"detail": _("This milestone plan can no longer be edited.")}
            )

        plan.note = data["note"]
        plan.suggestion_enabled = data["suggestion_enabled"]
        plan.currency = job_currency(job)
        plan.source_role = (
            MilestonePlan.SourceRole.CLIENT
            if role == "client"
            else MilestonePlan.SourceRole.FREELANCER
        )
        plan.save(
            update_fields=[
                "note",
                "suggestion_enabled",
                "currency",
                "source_role",
                "updated_at",
            ]
        )

        replace_plan_items_from_payload(plan, data["items"])
        validate_milestone_plan(plan)

        if role == "freelancer":
            plan.status = MilestonePlan.Status.PROPOSED
            plan.save(update_fields=["status", "updated_at"])

        plan.refresh_from_db()

        return Response(
            MilestonePlanSerializer(plan).data,
            status=status.HTTP_201_CREATED,
        )


class MilestonePlanDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, public_id: str) -> MilestonePlan:
        return get_object_or_404(
            MilestonePlan.objects.select_related(
                "job",
                "proposal",
                "created_by",
                "job__client__account__user",
            ).prefetch_related("items"),
            public_id=public_id,
        )

    def get(self, request, public_id: str):
        plan = self.get_object(public_id)
        ensure_job_party_access(plan.job, request.user, proposal=plan.proposal)

        return Response(
            MilestonePlanSerializer(plan).data,
            status=status.HTTP_200_OK,
        )

    @transaction.atomic
    def patch(self, request, public_id: str):
        plan = self.get_object(public_id)
        role = ensure_job_party_access(plan.job, request.user, proposal=plan.proposal)

        if plan.status not in EDITABLE_PLAN_STATUSES:
            raise ValidationError(
                {"detail": _("This milestone plan can no longer be edited.")}
            )

        if role == "freelancer":
            freelancer = user_freelancer_profile(request.user)
            if not plan.proposal or not freelancer or plan.proposal.freelancer_id != freelancer.id:
                raise PermissionDenied(
                    _("Only the freelancer who owns this proposal can edit this plan.")
                )

        serializer = MilestonePlanPatchSerializer(
            data=request.data,
            partial=True,
            context={"plan": plan},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        update_fields = ["updated_at"]

        if "note" in data:
            plan.note = data["note"]
            update_fields.append("note")

        if "suggestion_enabled" in data:
            plan.suggestion_enabled = data["suggestion_enabled"]
            update_fields.append("suggestion_enabled")

        if "items" in data:
            replace_plan_items_from_payload(plan, data["items"])

        plan.save(update_fields=update_fields)
        validate_milestone_plan(plan)

        if role == "freelancer" and plan.status == MilestonePlan.Status.DRAFT:
            plan.status = MilestonePlan.Status.PROPOSED
            plan.save(update_fields=["status", "updated_at"])

        plan.refresh_from_db()

        return Response(
            MilestonePlanSerializer(plan).data,
            status=status.HTTP_200_OK,
        )

    @transaction.atomic
    def delete(self, request, public_id: str):
        plan = self.get_object(public_id)
        ensure_job_party_access(plan.job, request.user, proposal=plan.proposal)

        if plan.status not in EDITABLE_PLAN_STATUSES:
            raise ValidationError(
                {"detail": _("This milestone plan can no longer be deleted.")}
            )

        plan.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MilestonePlanApproveView(APIView):
    """
    POST /api/milestones/milestone-plans/<public_id>/approve/

    Client explicitly approves a plan.
    If a shortlisted proposal exists, this creates the contract.
    """

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, public_id: str):
        plan = get_object_or_404(
            MilestonePlan.objects.select_related(
                "job",
                "proposal",
                "job__client__account__user",
            ).prefetch_related("items"),
            public_id=public_id,
        )

        ensure_job_party_access(plan.job, request.user, proposal=plan.proposal)

        client = user_client_profile(request.user)
        if not client or plan.job.client_id != client.id:
            raise PermissionDenied(_("Only the client can approve a milestone plan."))

        if plan.status == MilestonePlan.Status.CONVERTED:
            raise ValidationError(
                {"detail": _("This plan has already been converted into a contract.")}
            )

        validate_milestone_plan(plan)
        approve_milestone_plan(plan)

        shortlisted_proposal = plan.proposal

        if shortlisted_proposal is None:
            shortlisted_proposal = (
                Proposal.objects.filter(
                    job=plan.job,
                    status=Proposal.Status.SHORTLISTED,
                )
                .order_by("-created_at")
                .first()
            )

        if shortlisted_proposal is None:
            return Response(
                {
                    "detail": _(
                        "Plan approved. Shortlist a freelancer before creating the contract."
                    ),
                    "plan": MilestonePlanSerializer(plan).data,
                },
                status=status.HTTP_200_OK,
            )

        if shortlisted_proposal.status != Proposal.Status.SHORTLISTED:
            raise ValidationError(
                {
                    "detail": _(
                        "The selected proposal must still be shortlisted before a contract can be created."
                    )
                }
            )

        contract = create_contract_from_selected_plan(
            job=plan.job,
            plan=plan,
            proposal=shortlisted_proposal,
            created_by=request.user,
        )

        shortlisted_proposal.status = Proposal.Status.CONTRACTED
        shortlisted_proposal.save(update_fields=["status", "updated_at"])

        Proposal.objects.filter(job=plan.job).exclude(
            pk=shortlisted_proposal.pk
        ).filter(
            status__in=[
                Proposal.Status.PENDING,
                Proposal.Status.SHORTLISTED,
            ]
        ).update(status=Proposal.Status.REJECTED)

        plan.job.status = Job.Status.IN_PROGRESS
        plan.job.save(update_fields=["status", "updated_at"])

        return Response(
            ContractSerializer(contract).data,
            status=status.HTTP_201_CREATED,
        )


class MilestoneSubmissionCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, public_id: str):
        milestone = get_object_or_404(
            Milestone.objects.select_related(
                "contract",
                "contract__client",
                "contract__freelancer",
            ),
            public_id=public_id,
        )

        ensure_contract_party_access(milestone.contract, request.user)

        serializer = MilestoneActionSerializer(
            data=request.data,
            context={"action": "submit"},
        )
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        submission = submit_milestone(
            milestone=milestone,
            user=request.user,
            submission_note=data["submission_note"],
            submission_link=data.get("submission_link", ""),
            payload=data.get("payload", {}),
        )

        return Response(
            MilestoneSubmissionSerializer(submission).data,
            status=status.HTTP_201_CREATED,
        )


class MilestoneRevisionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, public_id: str):
        milestone = get_object_or_404(
            Milestone.objects.select_related("contract"),
            public_id=public_id,
        )

        ensure_contract_party_access(milestone.contract, request.user)

        serializer = MilestoneActionSerializer(
            data=request.data,
            context={"action": "request_revision"},
        )
        serializer.is_valid(raise_exception=True)

        updated = request_revision(
            milestone=milestone,
            user=request.user,
            revision_note=serializer.validated_data["revision_note"],
            revision_scope=serializer.validated_data["revision_scope"],
        )

        return Response(
            MilestoneSerializer(updated).data,
            status=status.HTTP_200_OK,
        )


class MilestoneApproveView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, public_id: str):
        milestone = get_object_or_404(
            Milestone.objects.select_related("contract"),
            public_id=public_id,
        )

        ensure_contract_party_access(milestone.contract, request.user)

        serializer = MilestoneActionSerializer(
            data=request.data,
            context={"action": "approve"},
        )
        serializer.is_valid(raise_exception=True)

        updated = approve_milestone(
            milestone=milestone,
            user=request.user,
            review_note=serializer.validated_data["review_note"],
        )

        return Response(
            MilestoneSerializer(updated).data,
            status=status.HTTP_200_OK,
        )


class MilestoneDisputeView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, public_id: str):
        milestone = get_object_or_404(
            Milestone.objects.select_related("contract"),
            public_id=public_id,
        )

        ensure_contract_party_access(milestone.contract, request.user)

        serializer = MilestoneActionSerializer(
            data=request.data,
            context={"action": "dispute"},
        )
        serializer.is_valid(raise_exception=True)

        updated = open_dispute(
            milestone=milestone,
            user=request.user,
            dispute_reason=serializer.validated_data["reason"],
        )

        return Response(
            MilestoneSerializer(updated).data,
            status=status.HTTP_200_OK,
        )


class MilestoneDeliverableRedirectView(APIView):
    """
    GET /api/milestones/milestones/<public_id>/deliverable/

    Redirect only if the milestone has a valid external submission link.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, public_id: str):
        milestone = get_object_or_404(
            Milestone.objects.select_related("contract"),
            public_id=public_id,
        )

        ensure_contract_party_access(milestone.contract, request.user)

        if not milestone.submission_link:
            raise ValidationError(
                {"detail": _("This milestone does not have a deliverable link yet.")}
            )

        return redirect(milestone.submission_link)