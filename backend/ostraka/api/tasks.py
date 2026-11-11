"""Tasks in an incident: assign to a team or a person, claim from a queue, tick off a checklist, nudge.
Also `GET /api/tasks/mine`, everything waiting on you across incidents."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import notify, work, workflow
from ostraka.api import links
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incident_schemas import MaybeVersioned
from ostraka.incidents import access, activity, views
from ostraka.incidents.refs import parse_ref
from ostraka.models import (
    FINISHED,
    ChecklistItem,
    Incident,
    IncidentPlaybook,
    IncidentStatus,
    Notification,
    NotificationKind,
    Role,
    Task,
    TaskDependency,
    TaskStatus,
    Team,
    User,
    utcnow,
)
from ostraka.work_schemas import (
    BlockerIn,
    ChecklistItemIn,
    ChecklistItemPatch,
    DecideIn,
    IncidentBrief,
    MyTaskOut,
    NudgeIn,
    TaskIn,
    TaskOut,
    TaskPatch,
)

router = APIRouter(prefix="/api/incidents/{incident_id}/tasks", tags=["tasks"])
mine_router = APIRouter(prefix="/api/tasks", tags=["tasks"])

NUDGE_GAP = timedelta(minutes=15)


async def _task(db: AsyncSession, incident: Incident, task_id: uuid.UUID) -> Task:
    task = await db.get(Task, task_id)
    if task is None or task.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That task isn't in this incident.")
    return task


def _owner_words(team: Team | None, assignee: User | None) -> str:
    if assignee and team:
        return f"{assignee.name} ({team.name})"
    return assignee.name if assignee else team.name if team else "nobody"


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[TaskOut]:
    incident = await access.load(db, user, incident_id)
    tasks = (
        (await db.scalars(select(Task).where(Task.incident_id == incident.id).order_by(Task.seq)))
        .unique()
        .all()
    )
    return [await work.task_out(db, t, user) for t in tasks]


async def create_task(
    db: AsyncSession,
    incident: Incident,
    user: User,
    body: TaskIn,
    *,
    origin: str = "",
) -> Task:
    """Add a task, record it and tell its owner; the caller commits with `activity.finish`.
    `origin` names what it came from ("from LSN-3") in the activity log."""
    parent = None
    if body.parent_id is not None:
        parent = await _task(db, incident, body.parent_id)
        if parent.parent_id is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Subtasks go one level deep.")
    team, assignee = await work.check_owner(db, user, incident, body.team_id, body.assignee_id)
    task = Task(
        incident_id=incident.id,
        parent_id=body.parent_id,
        title=body.title.strip(),
        description=body.description.strip(),
        team_id=team.id if team else None,
        assignee_id=assignee.id if assignee else None,
        due_at=body.due_at,
        created_by=user.id,
        checklist=[ChecklistItem(position=i, text=t) for i, t in enumerate(body.checklist)],
    )
    db.add(task)
    await db.flush()
    await db.refresh(task, ["team", "assignee"])
    await activity.record(
        db,
        incident,
        user,
        "task_added",
        f"{user.name} added {task.ref}{' under ' + parent.ref if parent else ''}"
        f"{' ' + origin if origin else ''} for {_owner_words(team, assignee)}: {task.title}",
        entity_type="task",
        entity_id=task.id,
    )
    await work.announce(db, incident, task, user)
    return task


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def add_task(
    incident_id: uuid.UUID,
    body: TaskIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> TaskOut:
    incident = await access.load(db, user, incident_id, edit=True)
    task = await create_task(db, incident, user, body)
    await activity.finish(db, user)
    return await work.task_out(db, task, user)


@router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    incident_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> TaskOut:
    incident = await access.load(db, user, incident_id, edit=True)
    task = await _task(db, incident, task_id)
    views.check_version(task.version, body.version, "task")
    sent = body.model_fields_set - {"version"}
    described: list[str] = []
    finishing = False

    if {"team_id", "assignee_id"} & sent:
        team_id = body.team_id if "team_id" in sent else task.team_id
        assignee_id = body.assignee_id if "assignee_id" in sent else task.assignee_id
        if team_id is None and assignee_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A task needs a team or a person.")
        if (team_id, assignee_id) != (task.team_id, task.assignee_id):
            team, assignee = await work.check_owner(db, user, incident, team_id, assignee_id)
            task.team_id, task.assignee_id = team_id, assignee_id
            described.append(f"gave it to {_owner_words(team, assignee)}")
            work.reset_reminders(task)
    for field in ("title", "description", "blocked_reason"):
        value = getattr(body, field)
        if field in sent and value is not None and value.strip() != getattr(task, field):
            setattr(task, field, value.strip())
            described.append("renamed it" if field == "title" else f"changed the {field.replace('_', ' ')}")
    if "due_at" in sent and body.due_at != task.due_at:
        task.due_at = body.due_at
        work.reset_reminders(task)
        described.append(f"due {body.due_at:%d %b %H:%M} UTC" if body.due_at else "removed the due date")
    if body.status is TaskStatus.DONE and task.outcomes and task.status is not TaskStatus.DONE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{task.ref} is a decision: choose one of its outcomes to finish it."
        )
    if body.status is not None and body.status is not task.status:
        before = task.status
        task.status = body.status
        if body.status in FINISHED:
            task.completed_at, task.completed_by = utcnow(), user.id
            finishing = True
        elif before in FINISHED:
            task.completed_at = task.completed_by = None
        if body.status is not TaskStatus.BLOCKED:
            task.blocked_reason = ""
        described.append(f"{before.value.replace('_', ' ')} → {body.status.value.replace('_', ' ')}")

    if described:
        reassigned = any(d.startswith("gave it to") for d in described)
        task.version += 1
        task.updated_at = utcnow()
        await db.flush()
        await db.refresh(task, ["team", "assignee"])
        await activity.record(
            db,
            incident,
            user,
            "task_updated",
            f"{user.name} updated {task.ref}: {'; '.join(described)}",
            entity_type="task",
            entity_id=task.id,
            reassigned=reassigned,
        )
        if reassigned:
            await work.announce(db, incident, task, user)
        if finishing:
            await work.release_dependents(db, task, user)
            await workflow.task_finished(db, incident, task, user)
        await activity.finish(db, user)
    return await work.task_out(db, task, user)


@router.post("/{task_id}/decide", response_model=TaskOut)
async def decide(
    incident_id: uuid.UUID,
    task_id: uuid.UUID,
    body: DecideIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> TaskOut:
    """Finish a decision by choosing an outcome. The outcome may close the record, move its phase or
    promote a hunt, and opens the playbook steps it names."""
    incident = await access.load(db, user, incident_id, edit=True)
    task = await _task(db, incident, task_id)
    views.check_version(task.version, body.version, "task")
    if not task.outcomes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{task.ref} isn't a decision.")
    if task.status in FINISHED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{task.ref} has already been decided.")
    outcome = await workflow.decide(db, incident, task, body.outcome, user)
    words = [f"{user.name} decided {task.ref}: {body.outcome}"]
    if outcome["action"] == "promote":
        if body.promote is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Name the incident and its severity to promote the hunt.",
            )
        promoted = await links.promote_hunt(db, user, incident, body.promote)
        words.append(f"promoted to {promoted.ref}")
    elif did := await workflow.act(db, incident, outcome, user):
        words.append(did)
    await activity.record(
        db,
        incident,
        user,
        "task_decided",
        "; ".join(words) + (f". {body.note.strip()}" if body.note.strip() else ""),
        entity_type="task",
        entity_id=task.id,
    )
    await work.release_dependents(db, task, user)
    if task.run_id:
        run = await db.get(IncidentPlaybook, task.run_id)
        await workflow.advance(db, incident, run, user)
    await activity.finish(db, user)
    await db.refresh(task, ["team", "assignee"])
    return await work.task_out(db, task, user)


@router.post("/{task_id}/claim", response_model=TaskOut)
async def claim(
    incident_id: uuid.UUID,
    task_id: uuid.UUID,
    body: MaybeVersioned | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> TaskOut:
    """Take a task from your team's queue."""
    incident = await access.load(db, user, incident_id, edit=True)
    task = await _task(db, incident, task_id)
    if body is not None and body.version is not None:
        views.check_version(task.version, body.version, "task")
    if task.status in FINISHED:
        raise HTTPException(status.HTTP_409_CONFLICT, "That task is already finished.")
    if task.assignee_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Someone has already claimed that task.")
    if task.team_id != user.team_id and user.role is not Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the task's team can claim it from the queue.")
    task.assignee_id = user.id
    if task.status is TaskStatus.OPEN:
        task.status = TaskStatus.IN_PROGRESS
    task.version += 1
    task.updated_at = utcnow()
    work.reset_reminders(task)
    await activity.record(
        db,
        incident,
        user,
        "task_claimed",
        f"{user.name} claimed {task.ref}",
        entity_type="task",
        entity_id=task.id,
    )
    await activity.finish(db, user)
    await db.refresh(task, ["assignee"])
    return await work.task_out(db, task, user)


@router.post("/{task_id}/release", response_model=TaskOut)
async def release(
    incident_id: uuid.UUID,
    task_id: uuid.UUID,
    body: MaybeVersioned | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> TaskOut:
    """Hand a claimed task back to its team's queue."""
    incident = await access.load(db, user, incident_id, edit=True)
    task = await _task(db, incident, task_id)
    if body is not None and body.version is not None:
        views.check_version(task.version, body.version, "task")
    if task.team_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "This task has no team queue to go back to."
        )
    if task.assignee_id != user.id:
        access.require_manage(user, incident)
    task.assignee_id = None
    if task.status is TaskStatus.IN_PROGRESS:
        task.status = TaskStatus.OPEN
    task.version += 1
    task.updated_at = utcnow()
    work.reset_reminders(task)
    await activity.record(
        db,
        incident,
        user,
        "task_released",
        f"{user.name} returned {task.ref} to the queue",
        entity_type="task",
        entity_id=task.id,
    )
    await db.flush()
    await work.announce(db, incident, task, user)
    await activity.finish(db, user)
    await db.refresh(task, ["assignee"])
    return await work.task_out(db, task, user)


# ---- checklist ------------------------------------------------------------------------------------


@router.post("/{task_id}/checklist", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def add_item(
    incident_id: uuid.UUID,
    task_id: uuid.UUID,
    body: ChecklistItemIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> TaskOut:
    incident = await access.load(db, user, incident_id, edit=True)
    task = await _task(db, incident, task_id)
    if len(task.checklist) >= 50:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A checklist holds at most 50 items.")
    position = max((i.position for i in task.checklist), default=-1) + 1
    task.checklist.append(ChecklistItem(position=position, text=body.text.strip()))
    task.updated_at = utcnow()
    await activity.record(
        db,
        incident,
        user,
        "checklist_changed",
        f"{user.name} added a checklist item to {task.ref}",
        entity_type="task",
        entity_id=task.id,
    )
    await activity.finish(db, user)
    return await work.task_out(db, task, user)


@router.patch("/{task_id}/checklist/{item_id}", response_model=TaskOut)
async def update_item(
    incident_id: uuid.UUID,
    task_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ChecklistItemPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> TaskOut:
    """Items are ticked independently, so two people working one checklist don't collide."""
    incident = await access.load(db, user, incident_id, edit=True)
    task = await _task(db, incident, task_id)
    item = next((i for i in task.checklist if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such checklist item.")
    views.check_version(item.version, body.version, "checklist item")
    what = []
    if body.text is not None and body.text.strip() != item.text:
        item.text = body.text.strip()
        what.append("edited")
    if body.done is not None and body.done != item.done:
        item.done = body.done
        item.done_by, item.done_at = (user.id, utcnow()) if body.done else (None, None)
        what.append("ticked" if body.done else "unticked")
    if what:
        item.version += 1
        task.updated_at = utcnow()
        await activity.record(
            db,
            incident,
            user,
            "checklist_changed",
            f"{user.name} {' and '.join(what)} “{item.text}” on {task.ref}",
            entity_type="task",
            entity_id=task.id,
        )
        await activity.finish(db, user)
    return await work.task_out(db, task, user)


@router.delete("/{task_id}/checklist/{item_id}", response_model=TaskOut)
async def remove_item(
    incident_id: uuid.UUID,
    task_id: uuid.UUID,
    item_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> TaskOut:
    incident = await access.load(db, user, incident_id, edit=True)
    task = await _task(db, incident, task_id)
    item = next((i for i in task.checklist if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such checklist item.")
    task.checklist.remove(item)
    task.updated_at = utcnow()
    await activity.record(
        db,
        incident,
        user,
        "checklist_changed",
        f"{user.name} removed “{item.text}” from {task.ref}",
        entity_type="task",
        entity_id=task.id,
    )
    await activity.finish(db, user)
    return await work.task_out(db, task, user)


# ---- waiting on other tasks ----------------------------------------------------------------------


async def _waits_on(db: AsyncSession, start: uuid.UUID, target: uuid.UUID) -> bool:
    """Whether `start` already waits, directly or through others, on `target`."""
    seen, frontier = set(), [start]
    while frontier:
        current = frontier.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        frontier += (
            await db.scalars(select(TaskDependency.blocked_by_id).where(TaskDependency.task_id == current))
        ).all()
    return False


@router.post("/{task_id}/blocked-by", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def add_blocker(
    incident_id: uuid.UUID,
    task_id: uuid.UUID,
    body: BlockerIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> TaskOut:
    """Say this task can't finish until another does. The other may be in any incident you can see."""
    incident = await access.load(db, user, incident_id, edit=True)
    task = await _task(db, incident, task_id)
    parsed = parse_ref(body.ref)
    blocker = (
        await db.scalar(select(Task).where(Task.seq == parsed.number))
        if parsed and parsed.kind == "task"
        else None
    )
    blocker_incident = await db.get(Incident, blocker.incident_id) if blocker else None
    if blocker is None or not access.can_view(user, blocker_incident):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No task {body.ref.lstrip('#')} that you can see.")
    if blocker.id == task.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A task can't wait on itself.")
    if await _waits_on(db, blocker.id, task.id):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{blocker.ref} already waits on {task.ref}.")
    if await db.get(TaskDependency, (task.id, blocker.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{task.ref} already waits on {blocker.ref}.")
    db.add(TaskDependency(task_id=task.id, blocked_by_id=blocker.id, created_by=user.id))
    where = "" if blocker_incident.id == incident.id else f" in {blocker_incident.ref}"
    await activity.record(
        db,
        incident,
        user,
        "task_dependency",
        f"{user.name} made {task.ref} wait on {blocker.ref}{where}",
        entity_type="task",
        entity_id=task.id,
    )
    await activity.finish(db, user)
    return await work.task_out(db, task, user)


@router.delete("/{task_id}/blocked-by/{blocker_id}", response_model=TaskOut)
async def remove_blocker(
    incident_id: uuid.UUID,
    task_id: uuid.UUID,
    blocker_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> TaskOut:
    incident = await access.load(db, user, incident_id, edit=True)
    task = await _task(db, incident, task_id)
    dependency = await db.get(TaskDependency, (task.id, blocker_id))
    if dependency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This task doesn't wait on that one.")
    blocker = await db.get(Task, blocker_id)
    await db.delete(dependency)
    await activity.record(
        db,
        incident,
        user,
        "task_dependency",
        f"{user.name} stopped {task.ref} waiting on {blocker.ref}",
        entity_type="task",
        entity_id=task.id,
    )
    await activity.finish(db, user)
    return await work.task_out(db, task, user)


# ---- nudges ---------------------------------------------------------------------------------------


@router.post("/{task_id}/nudge", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def nudge(
    incident_id: uuid.UUID,
    task_id: uuid.UUID,
    body: NudgeIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Ask a task's owner to look at it. One nudge per person per task every 15 minutes."""
    incident = await access.load(db, user, incident_id, edit=True)
    task = await _task(db, incident, task_id)
    if task.status in FINISHED:
        raise HTTPException(status.HTTP_409_CONFLICT, "That task is already finished.")
    recent = await db.scalar(
        select(Notification.id)
        .where(
            Notification.task_id == task.id,
            Notification.actor_id == user.id,
            Notification.kind == NotificationKind.NUDGE,
            Notification.created_at > utcnow() - NUDGE_GAP,
        )
        .limit(1)
    )
    if recent:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "You nudged this task in the last 15 minutes.")
    recipients = work.recipients_without(await work.owners(db, task), user.id)
    if not recipients:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "There's nobody else on this task to nudge."
        )
    message = body.message.strip()
    link = notify.incident_link(incident, "tasks", task.ref)
    title = f"{user.name} nudged you about {task.ref}: {task.title}"
    notify.send(
        db,
        recipients,
        NotificationKind.NUDGE,
        title,
        body=message,
        link=link,
        incident=incident,
        task=task,
        actor=user,
    )
    if task.assignee_id is None and task.team:
        notify.team_channel(
            db, task.team, NotificationKind.NUDGE, title, body=message, link=link, incident=incident
        )
    await activity.record(
        db,
        incident,
        user,
        "task_nudged",
        f"{user.name} nudged {task.ref}",
        entity_type="task",
        entity_id=task.id,
    )
    await activity.finish(db, user)


# ---- across incidents -----------------------------------------------------------------------------


@mine_router.get("/mine", response_model=list[MyTaskOut])
async def my_tasks(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[MyTaskOut]:
    """Unfinished tasks assigned to you, and unclaimed tasks in your team's queue, in open incidents
    you can see. Soonest due first."""
    mine = Task.assignee_id == user.id
    works_queue = user.team_id is not None and user.role is not Role.OBSERVER
    queued = (Task.assignee_id.is_(None) & (Task.team_id == user.team_id)) if works_queue else None
    rows = (
        (
            await db.execute(
                select(Task, Incident)
                .join(Incident, Incident.id == Task.incident_id)
                .where(
                    Task.status.not_in(FINISHED),
                    Incident.status == IncidentStatus.OPEN,
                    access.visible_filter(user),
                    or_(mine, queued) if queued is not None else mine,
                )
                .order_by(Task.due_at.asc().nulls_last(), Task.seq)
            )
        )
        .unique()
        .all()
    )
    out = []
    for task, incident in rows:
        base = await work.task_out(db, task, user)
        out.append(
            MyTaskOut(
                **base.model_dump(),
                incident=IncidentBrief(
                    id=incident.id,
                    kind=incident.kind,
                    ref=incident.ref,
                    title=incident.title,
                    severity=incident.severity,
                    tlp=incident.tlp,
                ),
                queued=task.assignee_id is None,
            )
        )
    return out
