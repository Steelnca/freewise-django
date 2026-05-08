from django.utils import timezone

from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from jobs.models import Job
from contracts.models import Contract, Milestone
from .models import Offer
from .serializers import OfferSerializer, OfferCreateSerializer


class SubmitOfferView(APIView):
    """
    POST /api/offers/<job_id>/  → freelancer submits an offer on a job
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

        if Offer.objects.filter(job=job, freelancer=freelancer).exists():
            return Response({'detail': 'You have already submitted an offer for this job.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = OfferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        offer = serializer.save(job=job, freelancer=freelancer)
        return Response(OfferSerializer(offer).data, status=status.HTTP_201_CREATED)


class MyOffersView(generics.ListAPIView):
    """
    GET /api/offers/mine/  → freelancer's submitted offers
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = OfferSerializer

    def get_queryset(self):
        account    = getattr(self.request.user, 'account', None)
        freelancer = getattr(account, 'freelancer_profile', None)
        if not freelancer:
            return Offer.objects.none()
        return Offer.objects.filter(freelancer=freelancer).select_related(
            'job', 'freelancer__account__user'
        )


class JobOffersView(generics.ListAPIView):
    """
    GET /api/offers/job/<job_id>/  → all offers on a job (client only, owner)
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = OfferSerializer

    def get_queryset(self):
        account = getattr(self.request.user, 'account', None)
        client  = getattr(account, 'client_profile', None)
        if not client:
            return Offer.objects.none()
        return Offer.objects.filter(
            job__id=self.kwargs['job_id'],
            job__client=client,
        ).select_related('freelancer__account__user', 'job')


class AcceptOfferView(APIView):
    """
    POST /api/offers/<offer_id>/accept/
    Client accepts an offer → creates Contract + Milestone.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, offer_id):
        account = getattr(request.user, 'account', None)
        client  = getattr(account, 'client_profile', None)
        if not client:
            return Response({'detail': 'Client profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            offer = Offer.objects.select_related('job', 'freelancer').get(
                pk=offer_id,
                job__client=client,
                status=Offer.Status.PENDING,
            )
        except Offer.DoesNotExist:
            return Response({'detail': 'Offer not found.'}, status=status.HTTP_404_NOT_FOUND)

        if hasattr(offer.job, 'contract'):
            return Response({'detail': 'This job already has a contract.'}, status=status.HTTP_400_BAD_REQUEST)

        # Accept this offer, reject all others on the same job
        offer.status = Offer.Status.ACCEPTED
        offer.save(update_fields=['status'])
        Offer.objects.filter(job=offer.job).exclude(pk=offer.pk).update(status=Offer.Status.REJECTED)

        # Mark job as in progress
        offer.job.status = Job.Status.IN_PROGRESS
        offer.job.save(update_fields=['status'])

        # Create contract
        contract = Contract.objects.create(
            job=offer.job,
            offer=offer,
            client=client,
            freelancer=offer.freelancer,
            agreed_price=offer.proposed_price,
            deadline=offer.job.deadline or timezone.now().date(),
        )

        # Create single milestone (full amount)
        Milestone.objects.create(
            contract=contract,
            title='Full project delivery',
            amount=offer.proposed_price,
            due_date=contract.deadline,
            order=1,
        )

        return Response({
            'detail': 'Offer accepted. Contract created.',
            'contract_id': contract.pk,
        }, status=status.HTTP_201_CREATED)


class WithdrawOfferView(APIView):
    """
    POST /api/offers/<offer_id>/withdraw/  → freelancer withdraws their offer
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, offer_id):
        account    = getattr(request.user, 'account', None)
        freelancer = getattr(account, 'freelancer_profile', None)
        if not freelancer:
            return Response({'detail': 'Freelancer profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            offer = Offer.objects.get(pk=offer_id, freelancer=freelancer, status=Offer.Status.PENDING)
        except Offer.DoesNotExist:
            return Response({'detail': 'Offer not found or cannot be withdrawn.'}, status=status.HTTP_404_NOT_FOUND)

        offer.status = Offer.Status.WITHDRAWN
        offer.save(update_fields=['status'])
        return Response({'detail': 'Offer withdrawn.'})