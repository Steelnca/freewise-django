from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

from contracts.models import Contract, Milestone
from jobs.models import Job
from proposals.models import Proposal


def user_client_profile(user):
    account = getattr(user, "account", None)
    return getattr(account, "client_profile", None)


def user_freelancer_profile(user):
    account = getattr(user, "account", None)
    return getattr(account, "freelancer_profile", None)


def job_client_profile(job):
    for attr in ("client", "client_profile", "owner", "client_user"):
        value = getattr(job, attr, None)
        if value is not None:
            return value
    return None


def ensure_contract_party_access(contract: Contract, user) -> None:
    client = user_client_profile(user)
    freelancer = user_freelancer_profile(user)

    if client and contract.client_id == client.id:
        return
    if freelancer and contract.freelancer_id == freelancer.id:
        return

    raise PermissionDenied(_("You are not allowed to access this contract."))


def ensure_milestone_access(milestone: Milestone, user) -> None:
    ensure_contract_party_access(milestone.contract, user)


def ensure_job_party_access(job: Job, user, proposal: Proposal | None = None) -> str:
    client = user_client_profile(user)
    freelancer = user_freelancer_profile(user)

    if client and job.client_id == client.id:
        return "client"

    if proposal is not None and freelancer and proposal.freelancer_id == freelancer.id:
        return "freelancer"

    if freelancer and proposal is None and job.proposals.filter(freelancer=freelancer).exists():
        return "freelancer"

    raise PermissionDenied(_("You are not allowed to access this job plan."))


def ensure_proposal_party_access(proposal: Proposal, user) -> None:
    client = user_client_profile(user)
    freelancer = user_freelancer_profile(user)
    job_client = job_client_profile(proposal.job)

    if client and job_client and getattr(job_client, "id", None) == client.id:
        return
    if freelancer and proposal.freelancer_id == freelancer.id:
        return

    raise PermissionDenied(_("You are not allowed to access this proposal."))
