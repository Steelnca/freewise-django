from __future__ import annotations

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db import transaction
from django.utils import timezone

from milestones.services import create_contract_from_selected_plan
from milestones.models import MilestonePlan
from jobs.models import Job

from .models import Proposal
from .serializers import ProposalSerializer

class MyProposalsView(generics.ListAPIView):
    """
    GET /api/proposals/mine/
    Freelancer's submitted proposals.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ProposalSerializer

    def get_queryset(self):
        account = getattr(self.request.user, "account", None)
        freelancer = getattr(account, "freelancer_profile", None)
        if not freelancer:
            return Proposal.objects.none()

        return (
            Proposal.objects.filter(freelancer=freelancer)
            .select_related("job", "freelancer__account__user")
            .prefetch_related("job__milestone_plans", "job__milestone_plans__items")
        )

class AcceptProposalView(APIView):
    """
    POST /api/proposals/<proposal_public_id>/accept/

    This now means: shortlist the freelancer.
    It does NOT create the contract unless a selected approved milestone plan already exists.
    """
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, public_id):
        account = getattr(request.user, "account", None)
        client = getattr(account, "client_profile", None)
        if not client:
            return Response(
                {"detail": "Client profile required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            proposal = Proposal.objects.select_related(
                "job",
                "freelancer",
                "job__client",
            ).get(
                public_id=public_id,
                job__client=client,
                status=Proposal.Status.PENDING,
            )
        except Proposal.DoesNotExist:
            return Response(
                {"detail": "Proposal not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if hasattr(proposal.job, "contract"):
            return Response(
                {"detail": "This job already has a contract."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Keep only one shortlisted proposal at a time.
        Proposal.objects.filter(
            job=proposal.job,
            status=Proposal.Status.SHORTLISTED,
        ).exclude(pk=proposal.pk).update(
            status=Proposal.Status.PENDING,
            shortlisted_at=None,
        )

        proposal.status = Proposal.Status.SHORTLISTED
        if hasattr(proposal, "shortlisted_at"):
            proposal.shortlisted_at = timezone.now()
            proposal.save(update_fields=["status", "shortlisted_at", "updated_at"])
        else:
            proposal.save(update_fields=["status", "updated_at"])

        selected_plan = (
            proposal.job.milestone_plans.filter(
                is_selected=True,
                status=MilestonePlan.Status.APPROVED,
            )
            .prefetch_related("items")
            .order_by("-created_at")
            .first()
        )

        # No plan yet: shortlist only. Job stays open.
        if selected_plan is None:
            return Response(
                {
                    "detail": "Proposal shortlisted. Milestone plan is still required.",
                    "proposal_public_id": proposal.public_id,
                    "requires_milestone_plan": True,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        # Plan exists and is approved: create the contract now.
        contract = create_contract_from_selected_plan(
            job=proposal.job,
            plan=selected_plan,
            proposal=proposal,
            created_by=request.user,
        )

        proposal.status = Proposal.Status.CONTRACTED
        if hasattr(proposal, "contracted_at"):
            proposal.contracted_at = timezone.now()
            proposal.save(update_fields=["status", "contracted_at", "updated_at"])
        else:
            proposal.save(update_fields=["status", "updated_at"])

        # Once the contract exists, the other bids are no longer active.
        Proposal.objects.filter(job=proposal.job).exclude(pk=proposal.pk).update(
            status=Proposal.Status.REJECTED
        )

        proposal.job.status = Job.Status.IN_PROGRESS
        proposal.job.save(update_fields=["status"])

        return Response(
            {
                "detail": "Proposal shortlisted and contract created.",
                "proposal_public_id": proposal.public_id,
                "contract_public_id": contract.public_id,
            },
            status=status.HTTP_201_CREATED,
        )

class RejectProposalView(APIView):
    """
    POST /api/proposals/<public_id>/reject/
    Client rejects a proposal.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, public_id):
        account = getattr(request.user, "account", None)
        client = getattr(account, "client_profile", None)
        if not client:
            return Response(
                {"detail": "Client profile required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            proposal = Proposal.objects.get(
                public_id=public_id,
                job__client=client,
                status__in=[
                    Proposal.Status.PENDING,
                    Proposal.Status.SHORTLISTED,
                ],
            )
        except Proposal.DoesNotExist:
            return Response(
                {"detail": "Proposal not found or cannot be rejected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        proposal.status = Proposal.Status.REJECTED
        proposal.save(update_fields=["status", "updated_at"])

        return Response({"detail": "Proposal rejected."}, status=status.HTTP_200_OK)

class WithdrawProposalView(APIView):
    """
    POST /api/proposals/<public_id>/withdraw/
    Freelancer withdraws their proposal.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, public_id):
        account = getattr(request.user, "account", None)
        freelancer = getattr(account, "freelancer_profile", None)
        if not freelancer:
            return Response(
                {"detail": "Freelancer profile required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            proposal = Proposal.objects.get(
                public_id=public_id,
                freelancer=freelancer,
                status__in=[
                    Proposal.Status.PENDING,
                    Proposal.Status.SHORTLISTED,
                ],
            )
        except Proposal.DoesNotExist:
            return Response(
                {"detail": "Proposal not found or cannot be withdrawn."},
                status=status.HTTP_404_NOT_FOUND,
            )

        proposal.status = Proposal.Status.WITHDRAWN
        proposal.save(update_fields=["status", "updated_at"])

        return Response({"detail": "Proposal withdrawn."}, status=status.HTTP_200_OK)

