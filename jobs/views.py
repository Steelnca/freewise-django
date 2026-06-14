from rest_framework import status, generics, filters
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.exceptions import PermissionDenied

from django.utils.translation import gettext_lazy as _

from .models import Job, Category, Tag
from .serializers import JobSerializer, JobCreateSerializer, CategorySerializer, TagSerializer


class IsClientPermission(IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        account = getattr(request.user, 'account', None)
        # require both client role flag and an actual client_profile
        if not account or not account.is_client:
            return False
        return getattr(account, 'client_profile', None) is not None


class JobListView(generics.ListAPIView):
    """
    GET /api/jobs/  → list all open jobs (public, filterable)
    """
    permission_classes = [AllowAny]
    serializer_class   = JobSerializer
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['title', 'description', 'category__name']
    ordering_fields    = ['created_at', 'budget_min', 'budget_max']
    ordering           = ['-created_at']

    def get_queryset(self):
        qs = Job.objects.filter(status=Job.Status.OPEN).select_related(
            'client__account__user', 'category'
        ).prefetch_related('tags', 'proposals')

        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category__slug=category)

        level = self.request.query_params.get('level')
        if level:
            qs = qs.filter(experience_level=level.upper())

        return qs


class JobCreateView(generics.CreateAPIView):
    """
    POST /api/jobs/  → create a job (clients only)
    Requires an active client profile — otherwise returns 403 with a clear message.
    """
    permission_classes = [IsClientPermission]
    serializer_class   = JobCreateSerializer

    def get_client_profile(self, request):
        # safer attribute access
        account = getattr(request.user, 'account', None)
        return getattr(account, 'client_profile', None)

    def perform_create(self, serializer):
        client_profile = self.get_client_profile(self.request)
        if not client_profile:
            # explicit 403 with clear message about needing an active client profile
            raise PermissionDenied(detail=_('You must have an active client profile to create a job.'))
        serializer.save(client=client_profile)


class JobDetailView(generics.RetrieveAPIView):
    """
    GET /api/jobs/<public_id>/  → job detail (public)
    """
    permission_classes = [AllowAny]
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    serializer_class   = JobSerializer
    queryset           = Job.objects.select_related(
        'client__account__user', 'category'
    ).prefetch_related('tags', 'proposals')


class JobUpdateView(generics.UpdateAPIView):
    """
    PUT /api/jobs/<public_id>/edit/  → update/cancel a job (owner only)
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = JobCreateSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    def get_queryset(self):
        account = getattr(self.request.user, 'account', None)
        client  = getattr(account, 'client_profile', None)
        if not client:
            return Job.objects.none()
        return Job.objects.filter(client=client, status=Job.Status.OPEN)


class MyJobsView(generics.ListAPIView):
    """
    GET /api/jobs/mine/  → current client's jobs
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = JobSerializer

    def get_queryset(self):
        account = getattr(self.request.user, 'account', None)
        client  = getattr(account, 'client_profile', None)
        if not client:
            return Job.objects.none()
        return Job.objects.filter(client=client).select_related(
            'client__account__user', 'category'
        ).prefetch_related('tags', 'proposals')


class CategoryListView(generics.ListAPIView):
    """GET /api/jobs/categories/"""
    permission_classes = [AllowAny]
    serializer_class   = CategorySerializer
    queryset           = Category.objects.all()