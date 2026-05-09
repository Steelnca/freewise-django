import json

from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny

from contracts.models import Milestone
from .models import EscrowTransaction, Payout, ChargilyWebhookLog
from .serializers import EscrowTransactionSerializer, PayoutSerializer
from . import chargily


class FundMilestoneView(APIView):
    """
    POST /api/payments/fund/<milestone_id>/
    Client initiates payment for a milestone → creates Chargily checkout.
    Returns checkout_url to redirect client to payment page.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, milestone_id):
        account = getattr(request.user, 'account', None)
        client  = getattr(account, 'client_profile', None)
        if not client:
            return Response({'detail': 'Client profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            milestone = Milestone.objects.select_related('contract__client').get(
                pk=milestone_id,
                contract__client=client,
                status=Milestone.Status.PENDING,
            )
        except Milestone.DoesNotExist:
            return Response({'detail': 'Milestone not found or already funded.'}, status=status.HTTP_404_NOT_FOUND)

        # Build redirect URLs (frontend handles these pages)
        base_url    = request.build_absolute_uri('/').rstrip('/')
        success_url = f"{base_url}/dashboard/payments/success"
        failure_url = f"{base_url}/dashboard/payments/failure"
        webhook_url = request.build_absolute_uri('/api/payments/webhook/')

        try:
            checkout = chargily.create_checkout(milestone, success_url, failure_url, webhook_url)
        except Exception as e:
            return Response({'detail': f'Payment gateway error: {str(e)}'}, status=status.HTTP_502_BAD_GATEWAY)

        fees = chargily.calculate_fees(float(milestone.amount))

        # Create escrow record
        escrow, _ = EscrowTransaction.objects.get_or_create(
            milestone=milestone,
            defaults={
                'amount':               milestone.amount,
                'platform_fee':         fees['platform_fee'],
                'freelancer_gets':      fees['freelancer_gets'],
                'chargily_checkout_id': checkout.get('id', ''),
                'status':               EscrowTransaction.Status.PENDING,
            }
        )

        return Response({
            'checkout_url': checkout.get('checkout_url'),
            'escrow_id':    escrow.pk,
        })


@method_decorator(csrf_exempt, name='dispatch')
class ChargilyWebhookView(APIView):
    """
    POST /api/payments/webhook/
    Receives Chargily payment events and updates escrow status.
    No auth — verified by signature instead.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        payload   = request.body
        signature = request.headers.get('signature', '')

        if not chargily.verify_webhook_signature(payload, signature):
            return Response({'detail': 'Invalid signature.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return Response({'detail': 'Invalid JSON.'}, status=status.HTTP_400_BAD_REQUEST)

        event_id   = data.get('id', '')
        event_type = data.get('type', '')

        # Idempotency — skip if already processed
        log, created = ChargilyWebhookLog.objects.get_or_create(
            event_id=event_id,
            defaults={'event_type': event_type, 'payload': data},
        )
        if not created and log.processed:
            return Response({'detail': 'Already processed.'})

        try:
            self._handle_event(event_type, data)
            log.processed = True
            log.save(update_fields=['processed'])
        except Exception as e:
            log.error = str(e)
            log.save(update_fields=['error'])
            return Response({'detail': 'Processing error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'detail': 'OK'})

    def _handle_event(self, event_type: str, data: dict):
        if event_type == 'checkout.paid':
            checkout_id = data.get('data', {}).get('id', '')
            payment_id  = data.get('data', {}).get('payment_id', '')

            try:
                escrow = EscrowTransaction.objects.get(chargily_checkout_id=checkout_id)
            except EscrowTransaction.DoesNotExist:
                return

            escrow.status            = EscrowTransaction.Status.HELD
            escrow.chargily_payment_id = payment_id
            escrow.paid_at           = timezone.now()
            escrow.save(update_fields=['status', 'chargily_payment_id', 'paid_at'])

            # Mark milestone as funded
            escrow.milestone.status = Milestone.Status.FUNDED
            escrow.milestone.save(update_fields=['status'])


class MyPayoutsView(generics.ListAPIView):
    """
    GET /api/payments/payouts/  → freelancer's payout history
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = PayoutSerializer

    def get_queryset(self):
        account    = getattr(self.request.user, 'account', None)
        freelancer = getattr(account, 'freelancer_profile', None)
        if not freelancer:
            return Payout.objects.none()
        return Payout.objects.filter(freelancer=freelancer).select_related('escrow')


class MyEscrowView(generics.ListAPIView):
    """
    GET /api/payments/escrow/  → client's escrow transactions
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = EscrowTransactionSerializer

    def get_queryset(self):
        account = getattr(self.request.user, 'account', None)
        client  = getattr(account, 'client_profile', None)
        if not client:
            return EscrowTransaction.objects.none()
        return EscrowTransaction.objects.filter(
            milestone__contract__client=client
        ).select_related('milestone__contract')