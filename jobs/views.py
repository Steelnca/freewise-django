from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import filters, generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Category, Job, Tag
from .serializers import (
    CategorySerializer,
    JobSerializer,
    JobWriteSerializer,
    TagSerializer,
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
            .annotate(proposal_count_value=Count("proposals", distinct=True))
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
            .annotate(proposal_count_value=Count("proposals", distinct=True))
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
            .annotate(proposal_count_value=Count("proposals", distinct=True))
        )