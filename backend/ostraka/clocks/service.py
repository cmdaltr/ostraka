"""Stored clocks and the rules: their deadlines as things stand, and how one is shown."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.auth import policy
from ostraka.clock_schemas import ClockOut, CustomMilestone, MilestoneOut
from ostraka.clocks import regimes
from ostraka.clocks.regimes import Due, Milestone, Regime
from ostraka.incidents import views
from ostraka.incidents.masking import for_viewer
from ostraka.models import Clock, ClockMilestone, Incident, IncidentRole, User


def regime_of(clock: Clock) -> Regime:
    return regimes.REGIMES[clock.regime.value]


def milestones(clock: Clock) -> tuple[Milestone, ...]:
    return regimes.milestones_of(regime_of(clock), clock.milestones or [])


def aware_at(clock: Clock, incident: Incident) -> datetime:
    return clock.starts_at or incident.aware_at


def marks(clock: Clock) -> dict[str, ClockMilestone]:
    return {m.key: m for m in clock.marks}


def dues(clock: Clock, incident: Incident) -> list[Due]:
    """Each milestone's deadline as things stand."""
    met = {m.key: m.met_at for m in clock.marks if m.met_at is not None}
    return regimes.schedule(
        milestones(clock),
        aware=aware_at(clock, incident),
        classified=clock.classified_at,
        met=met,
        zone=clock.timezone,
    )


def sync_marks(clock: Clock) -> None:
    """A row for every milestone the clock has, and none for any it no longer has."""
    wanted = [m.key for m in milestones(clock)]
    have = marks(clock)
    clock.marks = [
        have.get(key) or ClockMilestone(key=key, reference="", note="", nudged=0) for key in wanted
    ]


def settle_nudges(clock: Clock, incident: Incident, now: datetime) -> None:
    """After a change that moves deadlines, count the thresholds already passed as heard, so the
    next nudge is the next threshold rather than a burst for ones gone by."""
    by_key = marks(clock)
    for due in dues(clock, incident):
        mark = by_key.get(due.key)
        if mark is None or mark.met_at is not None or due.due is None or due.starts is None:
            continue
        mark.nudged = regimes.level(due.starts, due.due, now)


def role_in(user: User, incident: Incident) -> IncidentRole | None:
    return next((m.role for m in incident.members if m.user_id == user.id), None)


async def clock_out(
    db: AsyncSession, user: User, incident: Incident, clock: Clock, now: datetime
) -> ClockOut:
    by_key = marks(clock)
    computed = dues(clock, incident)
    lookup = await views.names(
        db, [clock.owner_id, clock.closed_by, clock.created_by, *(m.met_by for m in clock.marks)]
    )
    role = role_in(user, incident)
    regime = regime_of(clock)
    out = []
    for due in computed:
        mark = by_key.get(due.key)
        met_at = mark.met_at if mark else None
        out.append(
            MilestoneOut(
                key=due.key,
                name=due.name,
                due_at=due.due,
                starts_at=due.starts,
                provisional=due.provisional,
                counted=due.counted,
                state=regimes.state(due, met_at, now),
                met_at=met_at,
                met_by=views.person(lookup, mark.met_by) if mark else None,
                reference=for_viewer(user, mark.reference) if mark else "",
                note=for_viewer(user, mark.note) if mark else "",
            )
        )
    return ClockOut(
        id=clock.id,
        ref=clock.ref,
        regime=clock.regime,
        regime_name=regime.name,
        authority=regime.authority,
        title=for_viewer(user, clock.title),
        owner=views.person(lookup, clock.owner_id),
        aware_at=aware_at(clock, incident),
        starts_at=clock.starts_at,
        classified_at=clock.classified_at,
        needs_classification=regimes.needs_classification(milestones(clock)),
        timezone=clock.timezone,
        custom_milestones=[CustomMilestone(**d) for d in (clock.milestones or [])],
        milestones=out,
        closed_at=clock.closed_at,
        closed_by=views.person(lookup, clock.closed_by),
        close_reason=for_viewer(user, clock.close_reason),
        created_by=views.person(lookup, clock.created_by),
        created_at=clock.created_at,
        version=clock.version,
        can_manage=policy.can_manage_clocks(user, role) is None,
        can_mark=clock.closed_at is None
        and policy.can_mark_milestone(user, role, is_owner=clock.owner_id == user.id) is None,
    )
