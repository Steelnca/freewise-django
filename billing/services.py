from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import mail_admins
from django.db import transaction
from django.utils.translation import gettext as _

from contracts.models import Contract
from proposals.models import Proposal
from .models import SubscriptionPlan, FreelancerSubscription, ClientSubscription

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limits:
    max_open_bids: int = 0
    max_active_contracts: int = 0
    max_jobs_posted: int = 0
    max_active_jobs: int = 0
    plan_name: str = ""
    plan_slug: str = ""


def _report_missing_subscription(*, role: str, user_id: int | None, profile_id: int | None, reason: str) -> None:
    message = (
        f"[Billing] Missing or invalid subscription for role={role}. "
        f"user_id={user_id}, profile_id={profile_id}, reason={reason}"
    )
    logger.warning(message)

    try:
        mail_admins(
            subject=f"Freewise billing issue: missing {role.lower()} subscription",
            message=message,
            fail_silently=True,
        )
    except Exception:
        logger.exception("Failed to send billing alert email.")


def get_default_plan(role: str) -> SubscriptionPlan | None:
    return SubscriptionPlan.objects.filter(
        role=role,
        is_active=True,
        is_default=True,
    ).first()


@transaction.atomic
def ensure_freelancer_has_plan(freelancer) -> FreelancerSubscription:
    """
    Make sure the freelancer has an active plan.
    If missing, auto-attach the default plan.
    If no default exists, fail closed with a friendly message.
    """
    sub = getattr(freelancer, "subscription", None)
    if sub and sub.is_active_subscription and sub.plan and sub.plan.is_active:
        return sub

    default_plan = get_default_plan(SubscriptionPlan.Role.FREELANCER)
    if not default_plan:
        # Report issue to devs
        raise ValidationError({
            "detail": "Your freelancer plan is not ready yet. Please contact support."
        })

    if sub:
        sub.plan = default_plan
        sub.status = FreelancerSubscription.Status.ACTIVE
        sub.ends_at = None
        sub.save(update_fields=["plan", "status", "ends_at", "updated_at"])
        return sub

    return FreelancerSubscription.objects.create(
        freelancer=freelancer,
        plan=default_plan,
        status=FreelancerSubscription.Status.ACTIVE,
    )


@transaction.atomic
def ensure_client_has_plan(client) -> ClientSubscription:
    """
    Make sure the client has an active plan.
    If missing, auto-attach the default plan.
    If no default exists, fail closed with a friendly message.
    """
    sub = getattr(client, "subscription", None)
    if sub and sub.is_active_subscription and sub.plan and sub.plan.is_active:
        return sub

    default_plan = get_default_plan(SubscriptionPlan.Role.CLIENT)
    if not default_plan:
        raise ValidationError({
            "detail": "Your client plan is not ready yet. Please contact support."
        })

    if sub:
        sub.plan = default_plan
        sub.status = ClientSubscription.Status.ACTIVE
        sub.ends_at = None
        sub.save(update_fields=["plan", "status", "ends_at", "updated_at"])
        return sub

    return ClientSubscription.objects.create(
        client=client,
        plan=default_plan,
        status=ClientSubscription.Status.ACTIVE,
    )


def get_freelancer_limits(freelancer) -> Limits:
    """
    Hard-fail closed if the freelancer has no valid plan.
    """
    sub = ensure_freelancer_has_plan(freelancer)
    plan = sub.plan

    return Limits(
        max_open_bids=plan.max_open_bids,
        max_active_contracts=plan.max_active_contracts,
        plan_name=plan.name,
        plan_slug=plan.slug,
    )


def get_client_limits(client) -> Limits:
    """
    Hard-fail closed if the client has no valid plan.
    """
    sub = ensure_client_has_plan(client)
    plan = sub.plan
    print(plan)

    return Limits(
        max_jobs_posted=plan.max_jobs_posted,
        max_active_jobs=plan.max_active_jobs,
        plan_name=plan.name,
        plan_slug=plan.slug,
    )


def count_open_bids(freelancer) -> int:
    return Proposal.objects.filter(
        freelancer=freelancer,
        status__in=[Proposal.Status.PENDING, Proposal.Status.SHORTLISTED],
    ).count()


def count_active_contracts(freelancer) -> int:
    return Contract.objects.filter(
        freelancer=freelancer,
        status__in=[Contract.Status.PENDING_FUNDING, Contract.Status.IN_PROGRESS, Contract.Status.SUSPENDED],
    ).count()


def count_active_jobs(client) -> int:
    return Contract.objects.filter(
        client=client,
        status__in=[Contract.Status.PENDING_FUNDING, Contract.Status.IN_PROGRESS, Contract.Status.SUSPENDED],
    ).count()


def count_posted_jobs(client) -> int:
    from jobs.models import Job
    return Job.objects.filter(client=client).count()


def assert_can_create_proposal(freelancer) -> None:
    limits = get_freelancer_limits(freelancer)
    open_bids = count_open_bids(freelancer)

    if limits.max_open_bids is None:
        return

    if open_bids < limits.max_open_bids:
        return

    raise ValidationError({
        "detail": f"You reached your open bid limit ({limits.max_open_bids})."
    })


def assert_can_take_new_contract(freelancer) -> None:
    limits = get_freelancer_limits(freelancer)
    active_contracts = count_active_contracts(freelancer)

    if limits.max_open_bids is None:
        return

    if active_contracts < limits.max_active_contracts:
        return

    raise ValidationError({
        "detail": f"You reached your open bid limit ({limits.max_open_bids})."
    })



def assert_can_post_job(client) -> None:
    limits = get_client_limits(client)
    posted = count_posted_jobs(client)

    if limits.max_jobs_posted is None:
        return

    if limits.max_jobs_posted and posted < limits.max_jobs_posted:
        return

    raise ValidationError({
        "detail": f"You reached your posted job limit ({limits.max_jobs_posted})."
    })


def assert_can_keep_active_job_count(client) -> None:
    limits = get_client_limits(client)
    active = count_active_jobs(client)

    if limits.max_active_jobs is None:
        return

    if active < limits.max_active_jobs:
        return

    raise ValidationError({
        "detail": f"You reached your active job limit ({limits.max_active_jobs})."
    })

@transaction.atomic
def assign_default_subscription_for_role(user, role: str):
    account = getattr(user, "account", None)
    if not account:
        raise ValidationError({"detail": _("Account not found.")})

    if role == SubscriptionPlan.Role.FREELANCER:
        freelancer = getattr(account, "freelancer_profile", None)
        if not freelancer:
            raise ValidationError({"detail": _("Freelancer profile required.")})

        plan = (
            SubscriptionPlan.objects.filter(
                role=SubscriptionPlan.Role.FREELANCER,
                is_active=True,
                is_default=True,
            )
            .first()
            or SubscriptionPlan.objects.filter(
                role=SubscriptionPlan.Role.FREELANCER,
                is_active=True,
            ).order_by("price", "created_at").first()
        )

        if not plan:
            raise ValidationError({"detail": _("No default freelancer plan is configured.")})

        sub, created = FreelancerSubscription.objects.get_or_create(
            freelancer=freelancer,
            defaults={
                "plan": plan,
                "status": FreelancerSubscription.Status.ACTIVE,
            },
        )

        if not created and (
            sub.plan_id != plan.id
            or sub.status != FreelancerSubscription.Status.ACTIVE
            or not sub.is_active_subscription
        ):
            sub.plan = plan
            sub.status = FreelancerSubscription.Status.ACTIVE
            sub.ends_at = None
            sub.save(update_fields=["plan", "status", "ends_at", "updated_at"])

        return sub

    if role == SubscriptionPlan.Role.CLIENT:
        client = getattr(account, "client_profile", None)
        if not client:
            raise ValidationError({"detail": _("Client profile required.")})

        plan = (
            SubscriptionPlan.objects.filter(
                role=SubscriptionPlan.Role.CLIENT,
                is_active=True,
                is_default=True,
            )
            .first()
            or SubscriptionPlan.objects.filter(
                role=SubscriptionPlan.Role.CLIENT,
                is_active=True,
            ).order_by("price", "created_at").first()
        )

        if not plan:
            raise ValidationError({"detail": _("No default client plan is configured.")})

        sub, created = ClientSubscription.objects.get_or_create(
            client=client,
            defaults={
                "plan": plan,
                "status": ClientSubscription.Status.ACTIVE,
            },
        )

        if not created and (
            sub.plan_id != plan.id
            or sub.status != ClientSubscription.Status.ACTIVE
            or not sub.is_active_subscription
        ):
            sub.plan = plan
            sub.status = ClientSubscription.Status.ACTIVE
            sub.ends_at = None
            sub.save(update_fields=["plan", "status", "ends_at", "updated_at"])

        return sub

    raise ValidationError({"detail": _("Unsupported role.")})

def get_quota_payload_for_freelancer(freelancer) -> dict:
    limits = get_freelancer_limits(freelancer)
    open_bids = count_open_bids(freelancer)
    active_contracts = count_active_contracts(freelancer)

    return {
        "plan_name": limits.plan_name,
        "plan_slug": limits.plan_slug,
        "open_bids": open_bids,
        "active_contracts": active_contracts,
        "max_open_bids": limits.max_open_bids,
        "max_active_contracts": limits.max_active_contracts,
        "can_create_proposal": open_bids < limits.max_open_bids,
        "can_take_contract": active_contracts < limits.max_active_contracts,
    }


def get_quota_payload_for_client(client) -> dict:
    limits = get_client_limits(client)
    posted_jobs = count_posted_jobs(client)
    active_jobs = count_active_jobs(client)

    return {
        "plan_name": limits.plan_name,
        "plan_slug": limits.plan_slug,
        "posted_jobs": posted_jobs,
        "active_jobs": active_jobs,
        "max_jobs_posted": limits.max_jobs_posted,
        "max_active_jobs": limits.max_active_jobs,
        "can_post_job": not limits.max_jobs_posted or posted_jobs < limits.max_jobs_posted,
        "can_keep_active_job": not limits.max_active_jobs or active_jobs < limits.max_active_jobs,
    }