from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny

from contracts.models import Contract
from .models import Review
from .serializers import ReviewSerializer, ReviewCreateSerializer


class SubmitReviewView(APIView):
    """
    POST /api/reviews/<contract_id>/
    Either party submits a review after contract completion.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, contract_id):
        account = getattr(request.user, 'account', None)
        if not account:
            return Response({'detail': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            contract = Contract.objects.select_related(
                'client__account', 'freelancer__account'
            ).get(pk=contract_id, status=Contract.Status.COMPLETED)
        except Contract.DoesNotExist:
            return Response({'detail': 'Contract not found or not completed.'}, status=status.HTTP_404_NOT_FOUND)

        # Determine reviewer and reviewee
        if contract.client.account == account:
            reviewee = contract.freelancer.account
        elif contract.freelancer.account == account:
            reviewee = contract.client.account
        else:
            return Response({'detail': 'You are not a party to this contract.'}, status=status.HTTP_403_FORBIDDEN)

        if Review.objects.filter(contract=contract, reviewer=account).exists():
            return Response({'detail': 'You have already reviewed this contract.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = ReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = serializer.save(contract=contract, reviewer=account, reviewee=reviewee)
        return Response(ReviewSerializer(review).data, status=status.HTTP_201_CREATED)


class FreelancerReviewsView(generics.ListAPIView):
    """
    GET /api/reviews/freelancer/<slug>/  → public reviews for a freelancer
    """
    permission_classes = [AllowAny]
    serializer_class   = ReviewSerializer

    def get_queryset(self):
        return Review.objects.filter(
            reviewee__slug=self.kwargs['slug'],
            reviewee__is_freelancer=True,
        ).select_related('reviewer', 'reviewee')


class ClientReviewsView(generics.ListAPIView):
    """
    GET /api/reviews/client/<slug>/  → public reviews for a client
    """
    permission_classes = [AllowAny]
    serializer_class   = ReviewSerializer

    def get_queryset(self):
        return Review.objects.filter(
            reviewee__slug=self.kwargs['slug'],
            reviewee__is_client=True,
        ).select_related('reviewer', 'reviewee')