from __future__ import annotations

from rest_framework import filters, generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from proposals.models import Proposal
from proposals.serializers import ProposalSerializer, ProposalCreateSerializer

from .models import Category, Job, Tag
from .serializers import (
    CategorySerializer,
    JobSerializer,
    JobWriteSerializer,
    TagSerializer,
    JobApplicantWorkspaceSerializer,
)


def _get_client_profile(user):
    account = getattr(user, "account", None)
    return getattr(account, "client_profile", None)


def _ensure_client_profile(user):
    client_profile = _get_client_profile(user)
    if not client_profile:
        raise PermissionDenied("You must have a client profile to perform this action.")
    return client_profile


class JobListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = JobSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title", "description", "category__name", "tags__name"]
    ordering_fields = ["created_at", "budget_total", "proposal_count"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = (
            Job.objects.filter(status=Job.Status.OPEN)
            .select_related("client__account__user", "category")
            .prefetch_related("tags", "proposals", "milestone_plans", "milestone_plans__items")
            .annotate(proposal_count=Count("proposals", distinct=True))
        )

        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category__slug=category)

        level = self.request.query_params.get("level")
        if level:
            qs = qs.filter(experience_level=level.upper())

        return qs


class JobDetailView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = JobSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    def get_queryset(self):
        return (
            Job.objects.select_related("client__account__user", "category")
            .prefetch_related("tags", "proposals", "milestone_plans", "milestone_plans__items")
            .annotate(proposal_count=Count("proposals", distinct=True))
        )


class JobCreateView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = JobWriteSerializer

    def perform_create(self, serializer):
        client_profile = _ensure_client_profile(self.request.user)
        serializer.save(client=client_profile)


class JobUpdateView(generics.UpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = JobWriteSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    def get_queryset(self):
        client_profile = _get_client_profile(self.request.user)
        if not client_profile:
            return Job.objects.none()

        return Job.objects.filter(client=client_profile).prefetch_related("tags")

    def perform_update(self, serializer):
        _ensure_client_profile(self.request.user)
        serializer.save()


class JobDeleteView(generics.DestroyAPIView):
    permission_classes = [IsAuthenticated]
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    def get_queryset(self):
        client_profile = _get_client_profile(self.request.user)
        if not client_profile:
            return Job.objects.none()

        return Job.objects.filter(client=client_profile)

    def perform_destroy(self, instance):
        _ensure_client_profile(self.request.user)
        instance.delete()


class JobCategoriesView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = CategorySerializer

    def get_queryset(self):
        return Category.objects.all().order_by("name")


class JobTagsView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = TagSerializer

    def get_queryset(self):
        return Tag.objects.all().order_by("name")


class MyJobsView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = JobSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title", "description", "category__name", "tags__name"]
    ordering_fields = ["created_at", "budget_total", "proposal_count"]
    ordering = ["-created_at"]

    def get_queryset(self):
        client_profile = _get_client_profile(self.request.user)
        if not client_profile:
            return Job.objects.none()

        return (
            Job.objects.filter(client=client_profile)
            .select_related("client__account__user", "category")
            .prefetch_related("tags", "proposals", "milestone_plans", "milestone_plans__items")
            .annotate(proposal_count=Count("proposals", distinct=True))
        )

class JobApplicantsView(generics.ListAPIView):
    """
    GET /api/jobs/<public_id>/applicants
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
                job__public_id=self.kwargs["public_id"],
                job__client=client,
            )
            .select_related("freelancer__account__user", "job")
            .prefetch_related("job__milestone_plans", "job__milestone_plans__items")
        )

class JobApplicantWorkspaceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, public_id, proposal_public_id):
        proposal = get_object_or_404(
            Proposal.objects.select_related(
                "job",
                "contract",
                "freelancer__account__user",
            ).prefetch_related(
                "milestone_plans__items",
            ),
            public_id=proposal_public_id,
            job__public_id=public_id,
        )

        if proposal.job.client.account.user_id != request.user.id:
            raise PermissionDenied(
                _("You do not have access to this applicant.")
            )

        selected_plan = (
            proposal.milestone_plans.filter(is_selected=True).first()
            or proposal.job.milestone_plans.filter(is_selected=True).first()
        )

        contract = getattr(proposal, "contract", None)

        workspace = {
            "job": proposal.job,
            "proposal": proposal,
            "selected_plan": selected_plan,
            "contract": contract,
        }

        serializer = JobApplicantWorkspaceSerializer(workspace)

        return Response(serializer.data)

class JobApplicationSubmitView(APIView):
    """
    POST /api/jobs/<public_id>/submit
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

    def post(self, request, public_id):
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
            public_id=public_id,
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

        serializer = ProposalCreateSerializer(data=request.data, context={"job": job})
        serializer.is_valid(raise_exception=True)

        proposal = serializer.save(job=job, freelancer=freelancer)
        return Response(ProposalSerializer(proposal).data, status=status.HTTP_201_CREATED)
