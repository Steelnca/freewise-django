from decimal import Decimal

from core.utils import status_value
from jobs.models import Job
from proposals.models import Proposal

from .models import MilestonePlan

MAX_FIRST_MILESTONE_PERCENT = Decimal("35")
MIN_LAST_MILESTONE_PERCENT = Decimal("15")
MAX_MILESTONES = 10


APPROVED_PLAN_STATUSES = {
    value
    for value in (
        status_value(MilestonePlan.Status, "APPROVED"),
        status_value(MilestonePlan.Status, "LOCKED"),
        status_value(MilestonePlan.Status, "CONVERTED"),
    )
    if value is not None
}

DRAFT_PLAN_STATUSES = {
    value
    for value in (
        status_value(MilestonePlan.Status, "DRAFT"),
        status_value(MilestonePlan.Status, "PROPOSED"),
    )
    if value is not None
}

SELECTED_PROPOSAL_STATUSES = {
    value
    for value in (
        status_value(Proposal.Status, "SHORTLISTED"),
        status_value(Proposal.Status, "ACCEPTED"),
    )
    if value is not None
}

PLAN_DRAFT_STATUS = status_value(MilestonePlan.Status, "DRAFT", "PROPOSED")
PLAN_PROPOSED_STATUS = status_value(MilestonePlan.Status, "PROPOSED", "DRAFT")
PLAN_APPROVED_STATUS = status_value(MilestonePlan.Status, "APPROVED", "LOCKED", "CONVERTED")
PROPOSAL_CONTRACTED_STATUS = status_value(Proposal.Status, "CONTRACTED", "ACCEPTED")
PROPOSAL_REJECTED_STATUS = status_value(Proposal.Status, "REJECTED")
JOB_IN_PROGRESS_STATUS = status_value(Job.Status, "IN_PROGRESS", "OPEN")

EDITABLE_PLAN_STATUSES = {
    MilestonePlan.Status.DRAFT,
    MilestonePlan.Status.PROPOSED,
}