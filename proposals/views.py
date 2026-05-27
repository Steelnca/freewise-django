from django.utils import timezone

from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from jobs.models import Job
from contracts.models import Contract, Milestone
from .models import Proposal
from .serializers import ProposalSerializer, ProposalCreateSerializer


class SubmitProposalView(APIView):
    """
    POST /api/proposals/<job_id>/  → freelancer submits an proposal on a job
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        account  = getattr(request.user, 'account', None)
        if not account or not account.is_freelancer:
            return Response({'detail': 'Freelancer profile required.'}, status=status.HTTP_403_FORBIDDEN)

        freelancer = getattr(account, 'freelancer_profile', None)
        if not freelancer:
            return Response({'detail': 'Freelancer profile not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            job = Job.objects.get(pk=job_id, status=Job.Status.OPEN)
        except Job.DoesNotExist:
            return Response({'detail': 'Job not found or not open.'}, status=status.HTTP_404_NOT_FOUND)

        # prevent client from bidding on their own job
        if job.client.account == account:
            return Response({'detail': 'You cannot bid on your own job.'}, status=status.HTTP_400_BAD_REQUEST)

        if Proposal.objects.filter(job=job, freelancer=freelancer).exists():
            return Response({'detail': 'You have already submitted an proposal for this job.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = ProposalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        proposal = serializer.save(job=job, freelancer=freelancer)
        return Response(ProposalSerializer(proposal).data, status=status.HTTP_201_CREATED)


class MyProposalsView(generics.ListAPIView):
    """
    GET /api/proposals/mine/  → freelancer's submitted proposals
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = ProposalSerializer

    def get_queryset(self):
        account    = getattr(self.request.user, 'account', None)
        freelancer = getattr(account, 'freelancer_profile', None)
        if not freelancer:
            return Proposal.objects.none()
        return Proposal.objects.filter(freelancer=freelancer).select_related(
            'job', 'freelancer__account__user'
        )


class JobProposalsView(generics.ListAPIView):
    """
    GET /api/proposals/job/<job_id>/  → all proposals on a job (client only, owner)
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = ProposalSerializer

    def get_queryset(self):
        account = getattr(self.request.user, 'account', None)
        client  = getattr(account, 'client_profile', None)
        if not client:
            return Proposal.objects.none()
        return Proposal.objects.filter(
            job__id=self.kwargs['job_id'],
            job__client=client,
        ).select_related('freelancer__account__user', 'job')


class AcceptProposalView(APIView):
    """
    POST /api/proposals/<proposal_id>/accept/
    Client accepts a proposal → creates Contract + Milestone.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, proposal_id):
        account = getattr(request.user, "account", None)
        client = getattr(account, "client_profile", None)
        if not client:
            return Response(
                {"detail": "Client profile required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            proposal = Proposal.objects.select_related("job", "freelancer").get(
                pk=proposal_id,
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

        proposal.status = Proposal.Status.ACCEPTED
        proposal.save(update_fields=["status"])

        Proposal.objects.filter(job=proposal.job).exclude(pk=proposal.pk).update(
            status=Proposal.Status.REJECTED
        )

        proposal.job.status = Job.Status.IN_PROGRESS
        proposal.job.save(update_fields=["status"])

        contract = Contract.objects.create(
            job=proposal.job,
            proposal=proposal,
            client=client,
            freelancer=proposal.freelancer,
            agreed_price=proposal.proposed_price,
            deadline=proposal.job.deadline or timezone.now().date(),
            status=Contract.Status.PENDING_FUNDING,
        )

        Milestone.objects.create(
            contract=contract,
            title="Full project delivery",
            amount=proposal.proposed_price,
            due_date=contract.deadline,
            order=1,
            status=Milestone.Status.PENDING,
        )

        return Response(
            {
                "detail": "Proposal accepted. Contract created.",
                "contract_id": contract.pk,
            },
            status=status.HTTP_201_CREATED,
        )


class WithdrawProposalView(APIView):
    """
    POST /api/proposals/<proposal_id>/withdraw/  → freelancer withdraws their proposal
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, proposal_id):
        account    = getattr(request.user, 'account', None)
        freelancer = getattr(account, 'freelancer_profile', None)
        if not freelancer:
            return Response({'detail': 'Freelancer profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            proposal = Proposal.objects.get(pk=proposal_id, freelancer=freelancer, status=Proposal.Status.PENDING)
        except Proposal.DoesNotExist:
            return Response({'detail': 'Proposal not found or cannot be withdrawn.'}, status=status.HTTP_404_NOT_FOUND)

        proposal.status = Proposal.Status.WITHDRAWN
        proposal.save(update_fields=['status'])
        return Response({'detail': 'Proposal withdrawn.'})