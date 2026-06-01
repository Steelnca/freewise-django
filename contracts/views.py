"""
Freewise contract views.

These views do not mutate models directly.
They delegate all state changes to contracts/services.py.
"""

from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Contract, Milestone
from .serializers import ContractSerializer, MilestoneActionSerializer, MilestoneSerializer, MilestoneCreateSerializer, ContractEventSerializer
from .services import (
    approve_milestone,
    cancel_contract,
    ensure_party_access,
    get_party_contract_queryset,
    open_dispute,
    request_revision,
    submit_milestone,
    create_milestone,
    ensure_milestone_access,
)


class MyContractsView(generics.ListAPIView):
    """
    GET /api/contracts/
    All contracts where the current user is a party.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ContractSerializer

    def get_queryset(self):
        return get_party_contract_queryset(self.request.user)


class ContractDetailView(generics.RetrieveAPIView):
    """
    GET /api/contracts/<pk>/
    Contract detail for parties only.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ContractSerializer

    def get_queryset(self):
        return get_party_contract_queryset(self.request.user)


class SubmitMilestoneView(APIView):
    """
    POST /api/contracts/milestones/<pk>/submit/
    Freelancer submits milestone work.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        milestone = get_object_or_404(Milestone.objects.select_related("contract"), pk=pk)

        try:
            ensure_party_access(milestone.contract, request.user)
            serializer = MilestoneActionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            updated = submit_milestone(
                milestone=milestone,
                user=request.user,
                submission_note=serializer.validated_data.get("note", ""),
                submission_link=serializer.validated_data.get("submission_link", ""),
            )
        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(MilestoneSerializer(updated).data, status=status.HTTP_200_OK)


class RequestRevisionView(APIView):
    """
    POST /api/contracts/milestones/<pk>/request-revision/
    Client asks for a revision.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        milestone = get_object_or_404(Milestone.objects.select_related("contract"), pk=pk)

        try:
            ensure_party_access(milestone.contract, request.user)
            serializer = MilestoneActionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            updated = request_revision(
                milestone=milestone,
                user=request.user,
                review_note=serializer.validated_data.get("note", ""),
            )
        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(MilestoneSerializer(updated).data, status=status.HTTP_200_OK)


class ApproveMilestoneView(APIView):
    """
    POST /api/contracts/milestones/<pk>/approve/
    Client approves milestone and triggers escrow release.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        milestone = get_object_or_404(Milestone.objects.select_related("contract"), pk=pk)

        try:
            ensure_party_access(milestone.contract, request.user)
            serializer = MilestoneActionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            updated = approve_milestone(
                milestone=milestone,
                user=request.user,
                review_note=serializer.validated_data.get("note", ""),
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

    def post(self, request, pk):
        milestone = get_object_or_404(Milestone.objects.select_related("contract"), pk=pk)

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

    def post(self, request, pk):
        contract = get_object_or_404(Contract, pk=pk)

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


class CreateMilestoneView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        contract = get_object_or_404(Contract, pk=pk)
        ensure_party_access(contract, request.user)

        serializer = MilestoneCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            milestone = create_milestone(
                contract=contract,
                user=request.user,
                **serializer.validated_data,
            )
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(MilestoneSerializer(milestone).data, status=status.HTTP_201_CREATED)

class ContractEventsView(generics.ListAPIView):
    serializer_class = ContractEventSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        contract = get_object_or_404(Contract, pk=self.kwargs["pk"])
        ensure_party_access(contract, self.request.user)
        return contract.events.order_by("-created_at")


class MilestoneDeliverableRedirectView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        milestone = get_object_or_404(
            Milestone.objects.select_related("contract"),
            pk=pk,
        )

        ensure_milestone_access(milestone, request.user)

        if not milestone.submission_link:
            return Response(
                {"detail": "No deliverable link found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return redirect(milestone.submission_link)