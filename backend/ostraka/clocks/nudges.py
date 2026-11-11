"""Nudges as regulator deadlines approach, run from the scheduler's tick (under its advisory lock).

At half, three quarters and nine tenths of a milestone's time, and when its deadline passes, the
clock's owner hears; from nine tenths on, the incident commander does too (as they do straight away
when the clock has no owner). Each level goes once, and only the highest one reached: a scheduler that
was stopped for a while sends one nudge, not a burst. A breach is written to the activity log.

Clocks run until each milestone is met or the clock is closed, whether or not the incident is still
open: a final report can fall due after the response has finished. A milestone counted provisionally
(the one before it isn't in yet) isn't nudged: the one before it is. Quiet hours and the critical
exception are the notification channels' own (`profile.outside_channels`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import notify
from ostraka.clocks import regimes, service
from ostraka.incidents import access, activity
from ostraka.models import Clock, Incident, NotificationKind, User


def label(clock: Clock) -> str:
    name = service.regime_of(clock).name
    return f"{clock.ref} {name}" + (f" ({clock.title})" if clock.title else "")


async def tick(db: AsyncSession, now: datetime) -> int:
    """Send the nudges now due. Returns how many milestones it acted on; the caller commits."""
    rows = (
        await db.execute(
            select(Clock, Incident)
            .join(Incident, Incident.id == Clock.incident_id)
            .where(Clock.closed_at.is_(None))
        )
    ).all()
    acted = 0
    for clock, incident in rows:
        marks = service.marks(clock)
        for due in service.dues(clock, incident):
            mark = marks.get(due.key)
            if mark is None or mark.met_at is not None or due.due is None or due.starts is None:
                continue
            if due.provisional:
                continue
            reached = regimes.level(due.starts, due.due, now)
            if reached <= mark.nudged:
                continue
            await _nudge(db, clock, incident, due, reached, now)
            mark.nudged = reached
            mark.nudged_at = now
            acted += 1
    return acted


async def _nudge(
    db: AsyncSession, clock: Clock, incident: Incident, due: regimes.Due, reached: int, now: datetime
) -> None:
    assert due.due is not None and due.starts is not None
    owner = await db.get(User, clock.owner_id) if clock.owner_id else None
    commander_id = access.commander_id(incident)
    commander = await db.get(User, commander_id) if commander_id else None
    breached = reached == regimes.BREACHED
    what = f"{label(clock)}: {due.name.lower()}"
    if breached:
        kind = NotificationKind.CLOCK_BREACHED
        title = f"{what} is {regimes.remaining_words(now - due.due)} past its deadline"
    else:
        kind = NotificationKind.CLOCK_DUE
        share = round(regimes.NUDGE_AT[reached - 1] * 100)
        title = f"{what} is due in {regimes.remaining_words(due.due - now)} ({share}% of the time gone)"
    body = f"Due {due.due:%d %b %H:%M} UTC, {due.counted}, in {incident.ref}, {incident.title}."
    link = notify.incident_link(incident, "clocks", clock.ref)

    told: list[User] = []
    if owner is not None and owner.is_active:
        notify.send(db, [owner], kind, title, body=body, link=link, incident=incident)
        told.append(owner)
    if commander is not None and (reached >= regimes.COMMANDER_FROM or not told):
        if all(u.id != commander.id for u in told):
            role = "incident commander" + ("" if told else ", as the clock has no owner")
            notify.send(
                db,
                [commander],
                kind,
                title,
                body=f"{body} You're getting this as {role}.",
                link=link,
                incident=incident,
            )
            told.append(commander)
    if breached:
        names = ", ".join(u.name for u in told) or "nobody"
        await activity.record(
            db,
            incident,
            None,
            "clock_breached",
            f"Ostraka: {what} passed its deadline ({due.due:%d %b %H:%M} UTC) without being marked met. "
            f"Told: {names}",
            entity_type="clock",
            entity_id=clock.id,
            milestone=due.key,
        )
