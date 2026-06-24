
from decimal import Decimal

from .models import Job


MAX_MILESTONES = 10
FIRST_MILESTONE_MAX_PERCENT = Decimal("35")
LAST_MILESTONE_MIN_PERCENT = Decimal("15")

def _status_value(name: str):
    return getattr(Job.Status, name, None)


USER_EDITABLE_STATUSES = {
    s for s in (
        _status_value("DRAFT"),
        _status_value("OPEN"),
        _status_value("PAUSED"),
        _status_value("CLOSED"),
        _status_value("ARCHIVED"),
    ) if s is not None
}

SYSTEM_MANAGED_STATUSES = {
    s for s in (
        _status_value("SHORTLISTED"),
        _status_value("IN_PROGRESS"),
        _status_value("COMPLETED"),
    ) if s is not None
}

STATUS_TRANSITIONS = {
    _status_value("DRAFT"): {
        _status_value("DRAFT"),
        _status_value("OPEN"),
        _status_value("ARCHIVED"),
    },
    _status_value("OPEN"): {
        _status_value("OPEN"),
        _status_value("PAUSED"),
        _status_value("CLOSED"),
        _status_value("ARCHIVED"),
    },
    _status_value("PAUSED"): {
        _status_value("PAUSED"),
        _status_value("OPEN"),
        _status_value("CLOSED"),
        _status_value("ARCHIVED"),
    },
    _status_value("CLOSED"): {
        _status_value("CLOSED"),
        _status_value("ARCHIVED"),
    },
    _status_value("ARCHIVED"): {
        _status_value("ARCHIVED"),
    },
}
