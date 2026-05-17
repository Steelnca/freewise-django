from django.utils import timezone

from rest_framework import status, generics, filters
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny

from contracts.models import Contract, Milestone
from .models import Service, ServicePackage, Order
from .serializers import (
    ServiceSerializer, ServiceCreateSerializer,
    OrderSerializer, OrderCreateSerializer,
)


def get_freelancer(request):
    account = getattr(request.user, 'account', None)
    return getattr(account, 'freelancer_profile', None) if account else None


def get_client(request):
    account = getattr(request.user, 'account', None)
    return getattr(account, 'client_profile', None) if account else None


# ── Public service listing ────────────────────────────────────────────────────

class ServiceListView(generics.ListAPIView):
    """GET /api/services/ — browse active services (public)"""
    permission_classes = [AllowAny]
    serializer_class   = ServiceSerializer
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['title', 'description', 'category__name']
    ordering           = ['-created_at']

    def get_queryset(self):
        qs = Service.objects.filter(status=Service.Status.ACTIVE).select_related(
            'freelancer__account__user', 'category'
        ).prefetch_related('packages', 'tags')

        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category__slug=category)
        return qs


class ServiceDetailView(generics.RetrieveAPIView):
    """GET /api/services/<id>/ — single service detail (public)"""
    permission_classes = [AllowAny]
    serializer_class   = ServiceSerializer
    queryset           = Service.objects.select_related(
        'freelancer__account__user', 'category'
    ).prefetch_related('packages', 'tags')


# ── Freelancer service management ─────────────────────────────────────────────

class MyServicesView(generics.ListAPIView):
    """GET /api/services/mine/ — freelancer's own services"""
    permission_classes = [IsAuthenticated]
    serializer_class   = ServiceSerializer

    def get_queryset(self):
        freelancer = get_freelancer(self.request)
        if not freelancer:
            return Service.objects.none()
        return Service.objects.filter(freelancer=freelancer).select_related(
            'freelancer__account__user', 'category'
        ).prefetch_related('packages', 'tags')


class ServiceCreateView(APIView):
    """POST /api/services/create/"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        freelancer = get_freelancer(request)
        if not freelancer:
            return Response({'detail': 'Freelancer profile required.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ServiceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service = serializer.save(freelancer=freelancer)
        return Response(ServiceSerializer(service).data, status=status.HTTP_201_CREATED)


class ServiceUpdateView(APIView):
    """PUT /api/services/<id>/edit/"""
    permission_classes = [IsAuthenticated]

    def put(self, request, pk):
        freelancer = get_freelancer(request)
        if not freelancer:
            return Response({'detail': 'Freelancer profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            service = Service.objects.get(pk=pk, freelancer=freelancer)
        except Service.DoesNotExist:
            return Response({'detail': 'Service not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = ServiceCreateSerializer(service, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        service = serializer.save()
        return Response(ServiceSerializer(service).data)


class ServiceDeleteView(APIView):
    """DELETE /api/services/<id>/"""
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        freelancer = get_freelancer(request)
        if not freelancer:
            return Response({'detail': 'Freelancer profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            service = Service.objects.get(pk=pk, freelancer=freelancer)
        except Service.DoesNotExist:
            return Response({'detail': 'Service not found.'}, status=status.HTTP_404_NOT_FOUND)

        service.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Orders ────────────────────────────────────────────────────────────────────

class PlaceOrderView(APIView):
    """POST /api/services/<id>/order/ — client places an order"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        client = get_client(request)
        if not client:
            return Response({'detail': 'Client profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            service = Service.objects.get(pk=pk, status=Service.Status.ACTIVE)
        except Service.DoesNotExist:
            return Response({'detail': 'Service not found or not active.'}, status=status.HTTP_404_NOT_FOUND)

        # Prevent freelancer from ordering their own service
        if service.freelancer.account == client.account:
            return Response({'detail': 'You cannot order your own service.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            package = ServicePackage.objects.get(pk=serializer.validated_data['package_id'], service=service)
        except ServicePackage.DoesNotExist:
            return Response({'detail': 'Package not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Create order
        order = Order.objects.create(
            service=service,
            package=package,
            client=client,
            requirements=serializer.validated_data.get('requirements', ''),
        )

        # Auto-create a Contract + Milestone so escrow system works identically
        contract = Contract.objects.create(
            job=None,
            proposal=None,
            client=client,
            freelancer=service.freelancer,
            agreed_price=package.price,
            deadline=(timezone.now() + timezone.timedelta(days=package.delivery_days)).date(),
        )
        Milestone.objects.create(
            contract=contract,
            title=f"Deliver: {service.title} — {package.title}",
            amount=package.price,
            due_date=contract.deadline,
            order=1,
        )
        order.contract = contract
        order.status   = Order.Status.PENDING
        order.save(update_fields=['contract', 'status'])

        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


class MyOrdersView(generics.ListAPIView):
    """GET /api/orders/mine/ — orders for current user (client or freelancer)"""
    permission_classes = [IsAuthenticated]
    serializer_class   = OrderSerializer

    def get_queryset(self):
        account    = getattr(self.request.user, 'account', None)
        client     = getattr(account, 'client_profile',     None)
        freelancer = getattr(account, 'freelancer_profile', None)

        qs = Order.objects.select_related(
            'service__freelancer__account__user',
            'client__account__user',
            'package', 'contract',
        )
        if client and freelancer:
            return qs.filter(client=client) | qs.filter(service__freelancer=freelancer)
        elif client:
            return qs.filter(client=client)
        elif freelancer:
            return qs.filter(service__freelancer=freelancer)
        return Order.objects.none()


class OrderDetailView(generics.RetrieveAPIView):
    """GET /api/orders/<id>/"""
    permission_classes = [IsAuthenticated]
    serializer_class   = OrderSerializer

    def get_queryset(self):
        account    = getattr(self.request.user, 'account', None)
        client     = getattr(account, 'client_profile',     None)
        freelancer = getattr(account, 'freelancer_profile', None)
        qs = Order.objects.select_related('service__freelancer__account__user', 'client__account__user', 'package', 'contract')
        if client and freelancer:
            return qs.filter(client=client) | qs.filter(service__freelancer=freelancer)
        elif client:
            return qs.filter(client=client)
        elif freelancer:
            return qs.filter(service__freelancer=freelancer)
        return Order.objects.none()


class DeliverOrderView(APIView):
    """POST /api/orders/<id>/deliver/ — freelancer marks order as delivered"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        freelancer = get_freelancer(request)
        if not freelancer:
            return Response({'detail': 'Freelancer profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            order = Order.objects.get(pk=pk, service__freelancer=freelancer, status=Order.Status.ACTIVE)
        except Order.DoesNotExist:
            return Response({'detail': 'Order not found or not active.'}, status=status.HTTP_404_NOT_FOUND)

        order.status       = Order.Status.DELIVERED
        order.delivered_at = timezone.now()
        order.save(update_fields=['status', 'delivered_at'])

        # Mirror milestone status
        if order.contract:
            order.contract.milestones.filter(status='FUNDED').update(status='SUBMITTED')

        return Response(OrderSerializer(order).data)


class ApproveOrderView(APIView):
    """POST /api/orders/<id>/approve/ — client approves delivery"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        client = get_client(request)
        if not client:
            return Response({'detail': 'Client profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            order = Order.objects.select_related('contract').get(
                pk=pk, client=client, status=Order.Status.DELIVERED
            )
        except Order.DoesNotExist:
            return Response({'detail': 'Order not found or not delivered.'}, status=status.HTTP_404_NOT_FOUND)

        order.status       = Order.Status.COMPLETED
        order.completed_at = timezone.now()
        order.save(update_fields=['status', 'completed_at'])

        return Response(OrderSerializer(order).data)


class DisputeOrderView(APIView):
    """POST /api/orders/<id>/dispute/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        client = get_client(request)
        if not client:
            return Response({'detail': 'Client profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            order = Order.objects.get(pk=pk, client=client, status=Order.Status.DELIVERED)
        except Order.DoesNotExist:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        order.status = Order.Status.DISPUTED
        order.save(update_fields=['status'])
        return Response({'detail': 'Dispute opened.'})
