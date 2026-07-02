"""
Freewise contract views.

These views do not mutate models directly.
They delegate all state changes to contracts/services.py.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError, PermissionDenied

from core.access import (
    user_client_profile,
    user_freelancer_profile,
    ensure_contract_party_access,
)
from .models import Contract
from .serializers import ContractSerializer, ContractEventSerializer
from .services import cancel_contract


class ContractListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        contracts = (
            Contract.objects.select_related("proposal", "client", "freelancer")
            .prefetch_related("milestones")
            .order_by("-created_at")
        )

        client = user_client_profile(request.user)
        freelancer = user_freelancer_profile(request.user)

        if client and freelancer:
            contracts = contracts.filter(client=client) | contracts.filter(freelancer=freelancer)
        elif client:
            contracts = contracts.filter(client=client)
        elif freelancer:
            contracts = contracts.filter(freelancer=freelancer)
        else:
            contracts = Contract.objects.none()

        return Response(ContractSerializer(contracts.distinct(), many=True).data, status=status.HTTP_200_OK)

class ContractDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, public_id: str):
        contract = get_object_or_404(
            Contract.objects.select_related("proposal", "client", "freelancer")
            .prefetch_related("milestones", "milestones__submissions"),
            public_id=public_id,
        )
        ensure_contract_party_access(contract, request.user)
        return Response(ContractSerializer(contract).data, status=status.HTTP_200_OK)

class ContractCancelView(APIView):
    """
    POST /api/contracts/<pk>/cancel/
    Either party can cancel while the contract is not completed.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, public_id):
        contract = get_object_or_404(Contract, public_id=public_id)

        try:
            ensure_contract_party_access(contract, request.user)
            reason = (request.data.get("reason") or "").strip()
            updated = cancel_contract(contract=contract, user=request.user, reason=reason)
        except Exception as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(ContractSerializer(updated).data, status=status.HTTP_200_OK)

class ContractEventsView(generics.ListAPIView):
    serializer_class = ContractEventSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    def get_queryset(self):
        contract = get_object_or_404(Contract, public_id=self.kwargs["public_id"])
        ensure_contract_party_access(contract, self.request.user)
        return contract.events.order_by("-created_at")

