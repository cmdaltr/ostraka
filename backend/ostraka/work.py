"""Tasks: who owns one, who hears about it, and how it's shown. Shared by the tasks and playbooks
routers and by the nudge scheduler."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import notify
from ostraka.incident_schemas import TeamRef
from ostraka.incidents import access, activity, views
from ostraka.models import (
    FINISHED,
    Incident,
    IncidentMember,
    IncidentRole,
    IncidentTeam,
    NotificationKind,
    Role,
    Task,
    TaskDependency,
    Team,
    User,
    utcnow,
)
from ostraka.work_schemas import ChecklistItemOut, TaskLink, TaskOut


def is_overdue(task: Task) -> bool:
    return task.due_at is not None and task.status not in FINISHED and task.due_at <= utcnow()


async def _dependencies(
    db: AsyncSession, task: Task, viewer: User | None
) -> tuple[list[TaskLink], list[TaskLink], int]:
    """What this task waits on and what waits on it, as far as `viewer` can see."""
    lists: tuple[list[TaskLink], list[TaskLink]] = ([], [])
    hidden = 0
    for out, mine, theirs in (
        (lists[0], TaskDependency.task_id, TaskDependency.blocked_by_id),
        (lists[1], TaskDependency.blocked_by_id, TaskDependency.task_id),
    ):
        rows = (
            (
                await db.execute(
                    select(Task, Incident)
                    .join(TaskDependency, theirs == Task.id)
                    .join(Incident, Incident.id == Task.incident_id)
                    .where(mine == task.id)
                    .order_by(Task.seq)
                )
            )
            .unique()
            .all()
        )
        for other, incident in rows:
            if viewer is not None and not access.can_view(viewer, incident):
                hidden += 1
                continue
            out.append(
                TaskLink(
                    id=other.id,
                    ref=other.ref,
                    title=other.title,
                    status=other.status,
                    incident_ref=incident.ref,
                )
            )
    return lists[0], lists[1], hidden


async def task_out(db: AsyncSession, task: Task, viewer: User | None = None) -> TaskOut:
    blocked_by, blocks, hidden = await _dependencies(db, task, viewer)
    lookup = await views.names(
        db,
        [task.assignee_id, task.created_by, task.completed_by, *(i.done_by for i in task.checklist)],
    )
    return TaskOut(
        id=task.id,
        ref=task.ref,
        incident_id=task.incident_id,
        parent_id=task.parent_id,
        title=task.title,
        description=task.description,
        team=TeamRef(id=task.team.id, name=task.team.name) if task.team else None,
        assignee=views.person(lookup, task.assignee_id),
        status=task.status,
        blocked_reason=task.blocked_reason,
        due_at=task.due_at,
        overdue=is_overdue(task),
        checklist=[
            ChecklistItemOut(
                id=i.id,
                text=i.text,
                done=i.done,
                done_by=views.person(lookup, i.done_by),
                done_at=i.done_at,
                version=i.version,
            )
            for i in task.checklist
        ],
        blocked_by=blocked_by,
        blocks=blocks,
        hidden_links=hidden,
        playbook_id=task.playbook_id,
        outcomes=list(task.outcomes or []),
        outcome=task.outcome,
        created_by=views.person(lookup, task.created_by),
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_by=views.person(lookup, task.completed_by),
        completed_at=task.completed_at,
        version=task.version,
    )


async def team_members(db: AsyncSession, team_id: uuid.UUID, *, observers: bool = False) -> list[User]:
    """The people who work a team's queue: everyone in it except observers, unless asked for."""
    query = select(User).where(User.team_id == team_id, User.is_active)
    if not observers:
        query = query.where(User.role != Role.OBSERVER)
    return list((await db.scalars(query)).all())


async def team_leads(db: AsyncSession, team_id: uuid.UUID | None) -> list[User]:
    if team_id is None:
        return []
    return list(
        (
            await db.scalars(
                select(User).where(User.team_id == team_id, User.is_active, User.role == Role.TEAM_LEAD)
            )
        ).all()
    )


async def owners(db: AsyncSession, task: Task) -> list[User]:
    """Who a reminder about this task goes to: its assignee, or its team while it's unclaimed."""
    if task.assignee_id:
        assignee = await db.get(User, task.assignee_id)
        return [assignee] if assignee else []
    return await team_members(db, task.team_id) if task.team_id else []


async def check_owner(
    db: AsyncSession,
    user: User,
    incident: Incident,
    team_id: uuid.UUID | None,
    assignee_id: uuid.UUID | None,
) -> tuple[Team | None, User | None]:
    """Validate a task's team and assignee, bringing them into the incident if they aren't yet.
    Bringing someone in is a management action, so it needs the same permission as doing it directly."""
    team = await db.get(Team, team_id) if team_id else None
    if team_id and team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such team.")
    assignee = await db.get(User, assignee_id) if assignee_id else None
    if assignee_id and (assignee is None or not assignee.is_active):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person.")
    if assignee is not None and assignee.role is Role.OBSERVER:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Observers can follow an incident but not hold tasks."
        )

    if team is not None and not any(t.team_id == team.id for t in incident.teams):
        access.require_manage(user, incident)
        incident.teams.append(IncidentTeam(team_id=team.id))
        await activity.record(
            db,
            incident,
            user,
            "team_added",
            f"{user.name} involved {team.name} by giving it a task",
            entity_type="team",
            entity_id=team.id,
        )
    if assignee is not None and not access.can_view(assignee, incident):
        access.require_manage(user, incident)
        incident.members.append(IncidentMember(user_id=assignee.id, role=IncidentRole.RESPONDER))
        await activity.record(
            db,
            incident,
            user,
            "member_set",
            f"{user.name} added {assignee.name} by giving them a task",
            entity_type="user",
            entity_id=assignee.id,
            role=IncidentRole.RESPONDER.value,
        )
    return team, assignee


def _due(task: Task) -> str:
    return f" Due {task.due_at:%d %b %H:%M} UTC." if task.due_at else ""


async def announce(db: AsyncSession, incident: Incident, task: Task, actor: User) -> None:
    """Tell a task's new owner: the assignee, or the team's queue (its channel and its members)."""
    link = notify.incident_link(incident, "tasks", task.ref)
    title = f"{task.ref}: {task.title}"
    if task.assignee_id:
        assignee = await db.get(User, task.assignee_id)
        notify.send(
            db,
            [assignee],
            NotificationKind.ASSIGNED,
            title,
            body=f"{actor.name} gave you a task in {incident.ref}, {incident.title}.{_due(task)}",
            link=link,
            incident=incident,
            task=task,
            actor=actor,
        )
    elif task.team_id:
        team = await db.get(Team, task.team_id)
        body = (
            f"{actor.name} added a task to {team.name}'s queue in {incident.ref}, "
            f"{incident.title}.{_due(task)}"
        )
        notify.team_channel(
            db, team, NotificationKind.TEAM_TASK, title, body=body, link=link, incident=incident
        )
        notify.send(
            db,
            await team_members(db, team.id),
            NotificationKind.TEAM_TASK,
            title,
            body=body,
            link=link,
            incident=incident,
            task=task,
            actor=actor,
        )


def reset_reminders(task: Task) -> None:
    task.reminded_due_soon_at = task.reminded_overdue_at = None
    task.escalated_lead_at = task.escalated_commander_at = None


def recipients_without(users: Iterable[User], *exclude: uuid.UUID | None) -> list[User]:
    return [u for u in users if u.id not in exclude]


async def release_dependents(db: AsyncSession, task: Task, actor: User) -> None:
    """`task` has finished: tell the owners of tasks that were waiting only on it."""
    waiting = (
        (
            await db.scalars(
                select(Task)
                .join(TaskDependency, TaskDependency.task_id == Task.id)
                .where(TaskDependency.blocked_by_id == task.id, Task.status.not_in(FINISHED))
            )
        )
        .unique()
        .all()
    )
    for dependent in waiting:
        still = await db.scalar(
            select(Task.id)
            .join(TaskDependency, TaskDependency.blocked_by_id == Task.id)
            .where(TaskDependency.task_id == dependent.id, Task.status.not_in(FINISHED), Task.id != task.id)
            .limit(1)
        )
        if still:
            continue
        incident = await db.get(Incident, dependent.incident_id)
        await activity.record(
            db,
            incident,
            actor,
            "task_unblocked",
            f"{dependent.ref} can go ahead: {task.ref} is finished",
            entity_type="task",
            entity_id=dependent.id,
        )
        notify.send(
            db,
            await owners(db, dependent),
            NotificationKind.UNBLOCKED,
            f"{dependent.ref} can go ahead: {task.ref} is finished",
            body=f"{dependent.title}, in {incident.ref}.",
            link=notify.incident_link(incident, "tasks", dependent.ref),
            incident=incident,
            task=dependent,
            actor=actor,
        )
