from django.utils import timezone

from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import Contract, Milestone
from .serializers import ContractSerializer, MilestoneSerializer


class MyContractsView(generics.ListAPIView):
    """
    GET /api/contracts/  → all contracts for current user (as client or freelancer)
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = ContractSerializer

    def get_queryset(self):
        account = getattr(self.request.user, 'account', None)
        qs      = Contract.objects.select_related(
            'client__account__user',
            'freelancer__account__user',
            'job',
        ).prefetch_related('milestones')

        client     = getattr(account, 'client_profile',     None)
        freelancer = getattr(account, 'freelancer_profile', None)

        if client and freelancer:
            return qs.filter(client=client) | qs.filter(freelancer=freelancer)
        elif client:
            return qs.filter(client=client)
        elif freelancer:
            return qs.filter(freelancer=freelancer)
        return Contract.objects.none()


class ContractDetailView(generics.RetrieveAPIView):
    """
    GET /api/contracts/<id>/  → contract detail (parties only)
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = ContractSerializer

    def get_queryset(self):
        account    = getattr(self.request.user, 'account', None)
        client     = getattr(account, 'client_profile',     None)
        freelancer = getattr(account, 'freelancer_profile', None)
        qs = Contract.objects.select_related(
            'client__account__user', 'freelancer__account__user', 'job'
        ).prefetch_related('milestones')
        if client and freelancer:
            return qs.filter(client=client) | qs.filter(freelancer=freelancer)
        elif client:
            return qs.filter(client=client)
        elif freelancer:
            return qs.filter(freelancer=freelancer)
        return Contract.objects.none()


class SubmitMilestoneView(APIView):
    """
    POST /api/contracts/milestones/<id>/submit/
    Freelancer marks milestone as submitted.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        account    = getattr(request.user, 'account', None)
        freelancer = getattr(account, 'freelancer_profile', None)
        if not freelancer:
            return Response({'detail': 'Freelancer profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            milestone = Milestone.objects.get(pk=pk, contract__freelancer=freelancer, status=Milestone.Status.FUNDED)
        except Milestone.DoesNotExist:
            return Response({'detail': 'Milestone not found or not funded yet.'}, status=status.HTTP_404_NOT_FOUND)

        milestone.status       = Milestone.Status.SUBMITTED
        milestone.submitted_at = timezone.now()
        milestone.save(update_fields=['status', 'submitted_at'])
        return Response(MilestoneSerializer(milestone).data)


class ApproveMilestoneView(APIView):
    """
    POST /api/contracts/milestones/<id>/approve/
    Client approves milestone → triggers payout creation.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        account = getattr(request.user, 'account', None)
        client  = getattr(account, 'client_profile', None)
        if not client:
            return Response({'detail': 'Client profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            milestone = Milestone.objects.select_related('contract__freelancer').get(
                pk=pk, contract__client=client, status=Milestone.Status.SUBMITTED
            )
        except Milestone.DoesNotExist:
            return Response({'detail': 'Milestone not found or not submitted yet.'}, status=status.HTTP_404_NOT_FOUND)

        milestone.status      = Milestone.Status.APPROVED
        milestone.approved_at = timezone.now()
        milestone.save(update_fields=['status', 'approved_at'])

        # Trigger payout creation
        escrow = getattr(milestone, 'escrow', None)
        if escrow:
            from payments.models import Payout
            Payout.objects.get_or_create(
                escrow=escrow,
                defaults={
                    'freelancer': milestone.contract.freelancer,
                    'amount':     escrow.freelancer_gets,
                }
            )
            escrow.status      = escrow.Status.RELEASED
            escrow.released_at = timezone.now()
            escrow.save(update_fields=['status', 'released_at'])
            milestone.status = Milestone.Status.RELEASED
            milestone.save(update_fields=['status'])

        # Check if all milestones are released → complete the contract
        contract   = milestone.contract
        all_done   = not contract.milestones.exclude(status=Milestone.Status.RELEASED).exists()
        if all_done:
            contract.status       = Contract.Status.COMPLETED
            contract.completed_at = timezone.now()
            contract.save(update_fields=['status', 'completed_at'])

        return Response({'detail': 'Milestone approved. Payout queued.'})


class DisputeMilestoneView(APIView):
    """
    POST /api/contracts/milestones/<id>/dispute/
    Client opens a dispute on a submitted milestone.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        account = getattr(request.user, 'account', None)
        client  = getattr(account, 'client_profile', None)
        if not client:
            return Response({'detail': 'Client profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            milestone = Milestone.objects.get(
                pk=pk, contract__client=client, status=Milestone.Status.SUBMITTED
            )
        except Milestone.DoesNotExist:
            return Response({'detail': 'Milestone not found or cannot be disputed.'}, status=status.HTTP_404_NOT_FOUND)

        milestone.status = Milestone.Status.DISPUTED
        milestone.save(update_fields=['status'])

        milestone.contract.status = Contract.Status.DISPUTED
        milestone.contract.save(update_fields=['status'])

        return Response({'detail': 'Dispute opened. Platform will review.'})