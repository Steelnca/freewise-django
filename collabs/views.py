
from decimal import Decimal
from typing import Any, Dict, List, Optional

from rest_framework import status, generics, filters
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny

from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction

from contracts.models import Milestone

from .models import CollabRequest, CollabApplication
from .serializers import CollabRequestSerializer, CollabApplicationSerializer


# Helpers

def _payload(request) -> Dict[str, Any]:
    if isinstance(request.data, dict):
        return request.data
    return dict(request.data or {})


def _to_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _user_freelancer_profile(user):
    account = getattr(user, "account", None)
    return getattr(account, "freelancer_profile", None)


class CollabRequestCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, milestone_public_id: str):
        milestone = get_object_or_404(
            Milestone.objects.select_related("contract", "contract__freelancer", "contract__client"),
            public_id=milestone_public_id,
        )

        freelancer = _user_freelancer_profile(request.user)
        if not freelancer or milestone.contract.freelancer_id != freelancer.id:
            raise PermissionDenied(_("Only the lead freelancer can open a collab request."))

        payload = _payload(request)
        title = str(payload.get("title") or "").strip()
        seat_label = str(payload.get("seat_label") or "").strip()
        description = str(payload.get("description") or "").strip()
        visibility = str(payload.get("visibility") or "PUBLIC").upper().strip()
        seats_needed = int(payload.get("seats_needed") or 1)
        seat_amount = payload.get("seat_amount")

        if not title:
            raise ValidationError({"title": _("Collab title is required.")})
        if not seat_label:
            raise ValidationError({"seat_label": _("Seat label is required.")})
        if seats_needed <= 0:
            raise ValidationError({"seats_needed": _("Seats needed must be greater than zero.")})
        if seat_amount in (None, ""):
            raise ValidationError({"seat_amount": _("Seat amount is required.")})

        if visibility not in CollabRequest.Visibility.values:
            raise ValidationError({"visibility": _("Invalid collab visibility.")})

        collab = open_collab_request(
            milestone=milestone,
            created_by=request.user,
            title=title,
            seat_label=seat_label,
            seats_needed=seats_needed,
            seat_amount=_to_decimal(seat_amount),
            visibility=visibility,
            description=description,
        )

        return Response(CollabRequestSerializer(collab).data, status=status.HTTP_201_CREATED)


class CollabApplyView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, request_public_id: str):
        collab_request = get_object_or_404(
            CollabRequest.objects.select_related("milestone", "created_by", "milestone__contract"),
            public_id=request_public_id,
        )

        freelancer = _user_freelancer_profile(request.user)
        if not freelancer:
            raise PermissionDenied(_("Only freelancers can apply to collabs."))

        if collab_request.visibility == CollabRequest.Visibility.INVITE_ONLY:
            raise ValidationError(
                {"detail": _("This collab is invite-only. Invitation support will be added in the next step.")}
            )

        payload = _payload(request)
        note = str(payload.get("note") or "").strip()

        existing = CollabApplication.objects.filter(
            request=collab_request,
            freelancer=freelancer,
        ).first()

        if existing:
            if note and existing.status == CollabApplication.Status.PENDING:
                existing.note = note
                existing.save(update_fields=["note", "updated_at"])
            return Response(CollabApplicationSerializer(existing).data, status=status.HTTP_200_OK)

        application = apply_to_collab(
            request_obj=collab_request,
            freelancer=freelancer,
            note=note,
        )

        return Response(CollabApplicationSerializer(application).data, status=status.HTTP_201_CREATED)


class CollabAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, application_public_id: str):
        application = get_object_or_404(
            CollabApplication.objects.select_related(
                "request",
                "request__created_by",
                "request__milestone",
                "request__milestone__contract",
                "freelancer",
            ),
            public_id=application_public_id,
        )

        if application.request.created_by_id != request.user.id:
            raise PermissionDenied(_("Only the collab lead can accept applications."))

        application = accept_collab_application(application=application)
        return Response(CollabApplicationSerializer(application).data, status=status.HTTP_200_OK)