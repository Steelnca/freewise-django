from __future__ import annotations

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SubscriptionPlan, FreelancerSubscription, ClientSubscription
from .serializers import (
    ActivateSubscriptionSerializer,
    FreelancerQuotaSerializer,
    FreelancerSubscriptionSerializer,
    ClientQuotaSerializer,
    ClientSubscriptionSerializer,
    SubscriptionPlanSerializer,
)
from .services import (
    get_quota_payload_for_client,
    get_quota_payload_for_freelancer,
)


def _get_freelancer_profile(request):
    account = getattr(request.user, "account", None)
    return getattr(account, "freelancer_profile", None)


def _get_client_profile(request):
    account = getattr(request.user, "account", None)
    return getattr(account, "client_profile", None)


class SubscriptionPlanListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        role = request.query_params.get("role")
        qs = SubscriptionPlan.objects.filter(is_active=True)
        if role in {SubscriptionPlan.Role.FREELANCER, SubscriptionPlan.Role.CLIENT}:
            qs = qs.filter(role=role)
        return Response(SubscriptionPlanSerializer(qs.order_by("price", "created_at"), many=True).data)


class SubscriptionPlanDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, public_id: str):
        plan = SubscriptionPlan.objects.get(public_id=public_id, is_active=True)
        return Response(SubscriptionPlanSerializer(plan).data)


class MyFreelancerSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        freelancer = _get_freelancer_profile(request)
        if not freelancer:
            return Response({"detail": _("Freelancer profile required.")}, status=403)

        sub = getattr(freelancer, "subscription", None)
        if not sub:
            return Response({"detail": _("No active subscription.")}, status=404)

        return Response(FreelancerSubscriptionSerializer(sub).data)


class MyClientSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        client = _get_client_profile(request)
        if not client:
            return Response({"detail": _("Client profile required.")}, status=403)

        sub = getattr(client, "subscription", None)
        if not sub:
            return Response({"detail": _("No active subscription.")}, status=404)

        return Response(ClientSubscriptionSerializer(sub).data)


class ActivateFreelancerSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        freelancer = _get_freelancer_profile(request)
        if not freelancer:
            return Response({"detail": _("Freelancer profile required.")}, status=403)

        serializer = ActivateSubscriptionSerializer(
            data=request.data,
            context={"role": SubscriptionPlan.Role.FREELANCER},
        )
        serializer.is_valid(raise_exception=True)

        plan = serializer.validated_data["plan_public_id"]
        auto_renew = serializer.validated_data["auto_renew"]
        provider_name = serializer.validated_data["provider_name"]
        provider_reference = serializer.validated_data["provider_reference"]

        sub, _ = FreelancerSubscription.objects.update_or_create(
            freelancer=freelancer,
            defaults={
                "plan": plan,
                "status": FreelancerSubscription.Status.ACTIVE,
                "ends_at": None,
                "auto_renew": auto_renew,
                "provider_name": provider_name,
                "provider_reference": provider_reference,
            },
        )

        return Response(FreelancerSubscriptionSerializer(sub).data, status=200)


class ActivateClientSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        client = _get_client_profile(request)
        if not client:
            return Response({"detail": _("Client profile required.")}, status=403)

        serializer = ActivateSubscriptionSerializer(
            data=request.data,
            context={"role": SubscriptionPlan.Role.CLIENT},
        )
        serializer.is_valid(raise_exception=True)

        plan = serializer.validated_data["plan_public_id"]
        auto_renew = serializer.validated_data["auto_renew"]
        provider_name = serializer.validated_data["provider_name"]
        provider_reference = serializer.validated_data["provider_reference"]

        sub, _ = ClientSubscription.objects.update_or_create(
            client=client,
            defaults={
                "plan": plan,
                "status": ClientSubscription.Status.ACTIVE,
                "ends_at": None,
                "auto_renew": auto_renew,
                "provider_name": provider_name,
                "provider_reference": provider_reference,
            },
        )

        return Response(ClientSubscriptionSerializer(sub).data, status=200)


class MyQuotaView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        payload = {}
        freelancer = _get_freelancer_profile(request)
        client = _get_client_profile(request)

        if freelancer:
            payload["freelancer"] = get_quota_payload_for_freelancer(freelancer)

        if client:
            payload["client"] = get_quota_payload_for_client(client)

        if not payload:
            return Response({"detail": _("No role profile found.")}, status=403)

        return Response(payload)