from rest_framework import status, generics, filters
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny

from .models import Job, Category, Tag
from .serializers import JobSerializer, JobCreateSerializer, CategorySerializer, TagSerializer


class IsClientPermission(IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        account = getattr(request.user, 'account', None)
        return account and account.is_client


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
        ).prefetch_related('tags', 'offers')

        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category__slug=category)

        level = self.request.query_params.get('level')
        if level:
            qs = qs.filter(experience_level=level.upper())

        return qs


class JobCreateView(APIView):
    """
    POST /api/jobs/  → create a job (clients only)
    """
    permission_classes = [IsClientPermission]

    def post(self, request):
        client_profile = getattr(request.user.account, 'client_profile', None)
        if not client_profile:
            return Response({'detail': 'Client profile not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = JobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = serializer.save(client=client_profile)
        return Response(JobSerializer(job).data, status=status.HTTP_201_CREATED)


class JobDetailView(generics.RetrieveAPIView):
    """
    GET /api/jobs/<id>/  → job detail (public)
    """
    permission_classes = [AllowAny]
    serializer_class   = JobSerializer
    queryset           = Job.objects.select_related(
        'client__account__user', 'category'
    ).prefetch_related('tags', 'offers')


class JobUpdateView(generics.UpdateAPIView):
    """
    PUT /api/jobs/<id>/edit/  → update/cancel a job (owner only)
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = JobCreateSerializer

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
        ).prefetch_related('tags', 'offers')


class CategoryListView(generics.ListAPIView):
    """GET /api/jobs/categories/"""
    permission_classes = [AllowAny]
    serializer_class   = CategorySerializer
    queryset           = Category.objects.all()