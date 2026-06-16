from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from contracts.models import MilestonePlan
from contracts.services import create_contract_from_selected_plan
from jobs.models import Job

from .models import Proposal
from .serializers import ProposalCreateSerializer, ProposalSerializer


class SubmitProposalView(APIView):
    """
    POST /api/proposals/<job_public_id>/
    Freelancer submits a proposal on a job.

    Proposal submission stays lean:
    - cover letter
    - proposed price
    - delivery days

    Milestone plans are NOT created here anymore.
    They are handled later only if the freelancer is selected and the job does
    not already have a client-approved milestone plan.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, job_public_id):
        account = getattr(request.user, "account", None)
        if not account or not getattr(account, "is_freelancer", False):
            return Response(
                {"detail": "Freelancer profile required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        freelancer = getattr(account, "freelancer_profile", None)
        if not freelancer:
            return Response(
                {"detail": "Freelancer profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        job = get_object_or_404(
            Job.objects.select_related("client__account__user", "category").prefetch_related("milestone_plans", "milestone_plans__items"),
            public_id=job_public_id,
            status=Job.Status.OPEN,
        )

        # Prevent client from bidding on their own job.
        if getattr(job.client, "account", None) == account:
            return Response(
                {"detail": "You cannot bid on your own job."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Proposal.objects.filter(job=job, freelancer=freelancer).exists():
            return Response(
                {"detail": "You have already submitted a proposal for this job."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ProposalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        proposal = serializer.save(job=job, freelancer=freelancer)
        return Response(ProposalSerializer(proposal).data, status=status.HTTP_201_CREATED)


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


class JobProposalsView(generics.ListAPIView):
    """
    GET /api/proposals/job/<job_public_id>/
    All proposals on a job (client only, owner).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ProposalSerializer

    def get_queryset(self):
        account = getattr(self.request.user, "account", None)
        client = getattr(account, "client_profile", None)
        if not client:
            return Proposal.objects.none()

        return (
            Proposal.objects.filter(
                job__public_id=self.kwargs["job_public_id"],
                job__client=client,
            )
            .select_related("freelancer__account__user", "job")
            .prefetch_related("job__milestone_plans", "job__milestone_plans__items")
        )


class AcceptProposalView(APIView):
    """
    POST /api/proposals/<proposal_public_id>/accept/

    New flow:
    - accept the proposal
    - if a selected approved client plan exists, create the contract immediately
    - otherwise mark the proposal as AWAITING_PLAN
    - do NOT create any fallback milestone here
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

        selected_plan = (
            proposal.job.milestone_plans.filter(
                is_selected=True,
                status=MilestonePlan.Status.APPROVED,
            )
            .prefetch_related("items")
            .order_by("-created_at")
            .first()
        )

        proposal.status = Proposal.Status.ACCEPTED
        proposal.accepted_at = timezone.now() if hasattr(proposal, "accepted_at") else None

        # If there is no approved selected plan yet, hold the proposal open for plan creation.
        if selected_plan is None:
            if hasattr(Proposal.Status, "AWAITING_PLAN"):
                proposal.status = Proposal.Status.AWAITING_PLAN
            proposal.save(update_fields=["status"] + (["accepted_at"] if hasattr(proposal, "accepted_at") else []))

            Proposal.objects.filter(job=proposal.job).exclude(pk=proposal.pk).update(
                status=Proposal.Status.REJECTED
            )

            proposal.job.status = Job.Status.IN_PROGRESS
            proposal.job.save(update_fields=["status"])

            return Response(
                {
                    "detail": "Proposal accepted. A milestone plan is required before contract creation.",
                    "proposal_public_id": proposal.public_id,
                    "requires_milestone_plan": True,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        # A selected approved plan exists, so the contract can be created now.
        proposal.save(update_fields=["status"] + (["accepted_at"] if hasattr(proposal, "accepted_at") else []))

        Proposal.objects.filter(job=proposal.job).exclude(pk=proposal.pk).update(
            status=Proposal.Status.REJECTED
        )

        proposal.job.status = Job.Status.IN_PROGRESS
        proposal.job.save(update_fields=["status"])

        return Response(
            {
                "detail": "Proposal accepted. Contract created.",
                "proposal_public_id": proposal.public_id,
            },
            status=status.HTTP_201_CREATED,
        )


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
                status=Proposal.Status.PENDING,
            )
        except Proposal.DoesNotExist:
            return Response(
                {"detail": "Proposal not found or cannot be withdrawn."},
                status=status.HTTP_404_NOT_FOUND,
            )

        proposal.status = Proposal.Status.WITHDRAWN
        proposal.save(update_fields=["status"])
        return Response({"detail": "Proposal withdrawn."}, status=status.HTTP_200_OK)
