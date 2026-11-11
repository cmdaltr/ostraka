"""How often an incident's stakeholders hear from it: a fixed table by severity, which the commander
or comms may change for one incident."""

from __future__ import annotations

from datetime import datetime, timedelta

from ostraka.models import Severity, UpdatePlan

# Minutes between updates; None is "on change only": a draft appears when something changes
BY_SEVERITY: dict[Severity, int | None] = {
    Severity.CRITICAL: 2 * 60,
    Severity.HIGH: 4 * 60,
    Severity.MEDIUM: 24 * 60,
    Severity.LOW: None,
}
SHORTEST = 30  # minutes; an incident that needs more than this needs a call, not an email
LONGEST = 7 * 24 * 60


def every(severity: Severity, plan: UpdatePlan | None) -> int | None:
    """The minutes between this incident's updates, or None for on change only."""
    if plan is not None and plan.rhythm_set:
        return plan.every_minutes
    return BY_SEVERITY[severity]


def next_due(minutes: int | None, last: datetime) -> datetime | None:
    """When the next update is due, counted from the last one sent (or the declaration)."""
    return last + timedelta(minutes=minutes) if minutes else None


def words(minutes: int | None) -> str:
    """How the rhythm reads: every 4 h, every 30 min, daily, on change only."""
    if minutes is None:
        return "on change only"
    if minutes == 24 * 60:
        return "daily"
    if minutes % 60 == 0:
        return f"every {minutes // 60} h"
    return f"every {minutes} min"


def next_line(minutes: int | None) -> str:
    """The closing line of an update. Relative, so it reads the same whenever it's approved."""
    if minutes is None:
        return "Next update: when something material changes."
    span = f"{minutes // 60} h" if minutes % 60 == 0 else f"{minutes} min"
    return f"Next update: within {span}."
