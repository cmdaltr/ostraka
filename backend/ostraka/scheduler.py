"""Reminders and escalation for tasks with due dates, run in-process every minute.

Due soon: the owner. Overdue: the owner, again every few hours. Further overdue: the team lead, then
the incident commander. An unclaimed team task's owner is its queue: the team's members in ostraka and
its Teams channel. The intervals are settings (see `ostraka.config`).

Several backend processes may run this loop; a Postgres advisory lock lets one of them work each tick.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import notify, work
from ostraka.config import get_settings
from ostraka.db import sessions
from ostraka.incidents import access, activity
from ostraka.log import get_logger
from ostraka.models import FINISHED, Incident, IncidentStatus, NotificationKind, Task, User, utcnow

log = get_logger(__name__)

LOCK_ID = 24747_03  # any constant unique to ostraka's scheduler
_loop: asyncio.Task | None = None


def _hours(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    return f"{hours:.0f} h" if hours >= 1 else f"{delta.total_seconds() / 60:.0f} min"


async def _remind_owners(
    db: AsyncSession, incident: Incident, task: Task, kind: NotificationKind, title: str, body: str
) -> None:
    link = notify.incident_link(incident, "tasks", task.ref)
    notify.send(
        db, await work.owners(db, task), kind, title, body=body, link=link, incident=incident, task=task
    )
    if task.assignee_id is None and task.team:
        notify.team_channel(db, task.team, kind, title, body=body, link=link, incident=incident)


async def tick(db: AsyncSession, now: datetime | None = None) -> int:
    """One pass over tasks that are due soon or overdue. Returns how many tasks it acted on."""
    if not await db.scalar(select(func.pg_try_advisory_xact_lock(LOCK_ID))):
        return 0  # another process has this tick
    settings = get_settings()
    now = now or utcnow()
    due_soon = timedelta(minutes=settings.due_soon_minutes)
    repeat = timedelta(hours=settings.overdue_repeat_hours)
    to_lead = timedelta(hours=settings.escalate_lead_hours)
    to_commander = timedelta(hours=settings.escalate_commander_hours)

    rows = (
        (
            await db.execute(
                select(Task, Incident)
                .join(Incident, Incident.id == Task.incident_id)
                .where(
                    Task.status.not_in(FINISHED),
                    Task.due_at.is_not(None),
                    Task.due_at <= now + due_soon,
                    Incident.status == IncidentStatus.OPEN,
                )
                .order_by(Task.due_at)
            )
        )
        .unique()
        .all()
    )

    acted = 0
    for task, incident in rows:
        late = now - task.due_at
        where = f"in {incident.ref}, {incident.title}"
        if late < timedelta(0):
            if task.reminded_due_soon_at is None:
                await _remind_owners(
                    db,
                    incident,
                    task,
                    NotificationKind.DUE_SOON,
                    f"{task.ref} is due in {_hours(-late)}: {task.title}",
                    f"Due {task.due_at:%d %b %H:%M} UTC {where}.",
                )
                task.reminded_due_soon_at = now
                acted += 1
            continue

        if task.reminded_overdue_at is None or now - task.reminded_overdue_at >= repeat:
            await _remind_owners(
                db,
                incident,
                task,
                NotificationKind.OVERDUE,
                f"{task.ref} is {_hours(late)} overdue: {task.title}",
                f"It was due {task.due_at:%d %b %H:%M} UTC {where}.",
            )
            task.reminded_overdue_at = now
            acted += 1

        owners = {u.id for u in await work.owners(db, task)}
        assignee = await db.get(User, task.assignee_id) if task.assignee_id else None
        team_id = task.team_id or (assignee.team_id if assignee else None)
        leads = [u for u in await work.team_leads(db, team_id) if u.id not in owners]
        if late >= to_lead and task.escalated_lead_at is None:
            await _escalate(db, incident, task, leads, "team lead", late, where)
            task.escalated_lead_at = now
            acted += 1
        if late >= to_commander and task.escalated_commander_at is None:
            commander_id = access.commander_id(incident)
            # A commander who is also the owner or the team lead has heard already
            told = owners | {u.id for u in leads}
            commander = (
                await db.get(User, commander_id) if commander_id and commander_id not in told else None
            )
            await _escalate(
                db, incident, task, [commander] if commander else [], "incident commander", late, where
            )
            task.escalated_commander_at = now
            acted += 1

    await activity.finish(db)
    return acted


async def _escalate(
    db: AsyncSession, incident: Incident, task: Task, to: list[User], role: str, late: timedelta, where: str
) -> None:
    owner = task.assignee.name if task.assignee else (task.team.name + "'s queue" if task.team else "nobody")
    notify.send(
        db,
        to,
        NotificationKind.ESCALATED,
        f"{task.ref} is {_hours(late)} overdue with {owner}: {task.title}",
        body=f"You're getting this as {role} {where}.",
        link=notify.incident_link(incident, "tasks", task.ref),
        incident=incident,
        task=task,
    )
    names = ", ".join(u.name for u in to) or f"no {role}"
    await activity.record(
        db,
        incident,
        None,
        "task_escalated",
        f"Ostraka escalated {task.ref} to the {role} ({names}): {_hours(late)} overdue",
        entity_type="task",
        entity_id=task.id,
    )


async def _run() -> None:
    interval = get_settings().scheduler_seconds
    while True:
        try:
            async with sessions()() as db:
                await tick(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)


def startup() -> None:
    global _loop
    if get_settings().scheduler_enabled and _loop is None:
        _loop = asyncio.create_task(_run())


async def shutdown() -> None:
    global _loop
    if _loop is not None:
        _loop.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _loop
        _loop = None
    await notify.wait()
