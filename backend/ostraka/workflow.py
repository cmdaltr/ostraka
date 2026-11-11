"""Playbooks as workflows: which steps are ready, and what happens when a step finishes or a decision
is made.

Applying a playbook copies its steps onto the record (`IncidentPlaybook.steps`), each with a state:

    waiting   not ready yet: an earlier step, a phase or a decision stands in the way
    open      its task exists
    done      its task finished (done, cancelled, or decided)
    skipped   it can't happen now: the decision went the other way, or a step it follows was skipped

A step opens when every step in its `after` is done, the record has reached its `phase`, and, if an
outcome names it, that outcome was chosen. Opening a step creates its task and tells the team: that
notice is the hand-off. A decision's outcome can act on the record (close it, move its phase,
promote a hunt) and open steps; naming an earlier step runs it again, with everything after it.

Everything here is written with the caller's commit, after the change that set it off.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import notify, work
from ostraka.incidents import activity
from ostraka.log import get_logger
from ostraka.models import (
    PHASES_OF,
    ChecklistItem,
    Incident,
    IncidentPlaybook,
    IncidentStatus,
    IncidentTeam,
    NotificationKind,
    Playbook,
    Task,
    TaskStatus,
    Team,
    User,
    utcnow,
)

log = get_logger(__name__)

WAITING, OPEN, DONE, SKIPPED = "waiting", "open", "done", "skipped"
ACTIONS = ("none", "close", "phase", "promote")


def plan(playbook: Playbook) -> list[dict[str, Any]]:
    """The steps as they are now, each with a fresh state, to be kept on the record."""
    return [
        {
            "position": s.position,
            "title": s.title,
            "description": s.description,
            "team_id": str(s.team_id) if s.team_id else None,
            "due_minutes": s.due_minutes,
            "checklist": list(s.checklist),
            "after": list(s.after),
            "phase": s.phase,
            "outcomes": list(s.outcomes),
            "state": WAITING,
            "task_id": None,
            "outcome": None,
            "armed": False,  # an outcome chose it
            "runs": 0,
        }
        for s in playbook.steps
    ]


def gated(steps: list[dict[str, Any]]) -> set[int]:
    """Steps that only open when an outcome names them."""
    return {p for s in steps for o in s["outcomes"] for p in o["opens"] if p > s["position"]}


def _reached(incident: Incident, phase: str | None) -> bool:
    if not phase:
        return True
    order = [p.value for p in PHASES_OF[incident.kind]]
    return phase in order and order.index(incident.phase.value) >= order.index(phase)


def _ready(
    step: dict[str, Any], steps: list[dict[str, Any]], conditional: set[int], incident: Incident
) -> bool:
    return (
        step["state"] == WAITING
        and all(steps[a]["state"] == DONE for a in step["after"])
        and _reached(incident, step["phase"])
        and (step["position"] not in conditional or step["armed"])
    )


def _skip_followers(steps: list[dict[str, Any]]) -> None:
    """A step that follows a skipped step can't happen either, unless an outcome chose it."""
    changed = True
    while changed:
        changed = False
        for s in steps:
            if (
                s["state"] == WAITING
                and not s["armed"]
                and any(steps[a]["state"] == SKIPPED for a in s["after"])
            ):
                s["state"], changed = SKIPPED, True


async def _owner(
    db: AsyncSession, incident: Incident, run: IncidentPlaybook, step: dict[str, Any], actor: User
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """The step's team, brought into the record by the playbook if it isn't yet; or, with no team, the
    person who applied the playbook."""
    if not step["team_id"]:
        return None, run.applied_by or actor.id
    team_id = uuid.UUID(step["team_id"])
    team = await db.get(Team, team_id)
    if team is None:  # deleted since the playbook was applied
        return None, run.applied_by or actor.id
    if not any(t.team_id == team_id for t in incident.teams):
        incident.teams.append(IncidentTeam(team_id=team_id))
        await activity.record(
            db,
            incident,
            actor,
            "team_added",
            f"The playbook brought in {team.name} for its next step",
            entity_type="team",
            entity_id=team_id,
        )
    return team_id, None


async def advance(db: AsyncSession, incident: Incident, run: IncidentPlaybook, actor: User) -> list[Task]:
    """Open every step that's ready, and tell each team what's now theirs."""
    steps = [dict(s) for s in run.steps]
    conditional = gated(steps)
    _skip_followers(steps)
    opened: list[Task] = []
    now = utcnow()
    for step in steps:
        if not _ready(step, steps, conditional, incident):
            continue
        team_id, assignee_id = await _owner(db, incident, run, step, actor)
        task = Task(
            incident_id=incident.id,
            title=step["title"],
            description=step["description"],
            team_id=team_id,
            assignee_id=assignee_id,
            due_at=now + timedelta(minutes=step["due_minutes"]) if step["due_minutes"] is not None else None,
            playbook_id=run.playbook_id,
            run_id=run.id,
            step=step["position"],
            outcomes=[o["label"] for o in step["outcomes"]],
            created_by=actor.id,
            checklist=[ChecklistItem(position=i, text=t) for i, t in enumerate(step["checklist"])],
        )
        db.add(task)
        await db.flush()
        step["state"], step["task_id"], step["runs"] = OPEN, str(task.id), step["runs"] + 1
        opened.append(task)
    run.steps = steps  # a new list, so the JSON column is written
    for task in opened:
        await db.refresh(task, ["team", "assignee"])
    await _hand_over(db, incident, run, opened, actor)
    return opened


async def _hand_over(
    db: AsyncSession, incident: Incident, run: IncidentPlaybook, opened: list[Task], actor: User
) -> None:
    """One notice per team for the steps that just opened. Steps for a person are announced to them."""
    if not opened:
        return
    name = await db.scalar(select(Playbook.name).where(Playbook.id == run.playbook_id)) or "the playbook"
    await activity.record(
        db,
        incident,
        actor,
        "workflow_advanced",
        f"{name}: "
        + "; ".join(
            f"{t.ref} {t.title} is ready for {t.team.name if t.team else t.assignee.name}" for t in opened
        ),
        entity_type="task",
    )
    by_team: dict[uuid.UUID, list[Task]] = defaultdict(list)
    for task in opened:
        if task.team_id:
            by_team[task.team_id].append(task)
        elif task.assignee_id and task.assignee_id != actor.id:
            await work.announce(db, incident, task, actor)
    link = notify.incident_link(incident, "tasks")
    for team_id, ready in by_team.items():
        team = await db.get(Team, team_id)
        title = (
            f"Ready for {team.name}: {ready[0].ref} {ready[0].title}"
            if len(ready) == 1
            else f"{len(ready)} steps of {name} ready for {team.name}"
        )
        body = f"{incident.ref}, {incident.title}: " + "; ".join(t.title for t in ready)
        notify.team_channel(
            db, team, NotificationKind.TEAM_TASK, title, body=body, link=link, incident=incident
        )
        notify.send(
            db,
            await work.team_members(db, team_id),
            NotificationKind.TEAM_TASK,
            title,
            body=body,
            link=link,
            incident=incident,
            task=ready[0] if len(ready) == 1 else None,
            actor=actor,
        )


async def runs(db: AsyncSession, incident: Incident) -> list[IncidentPlaybook]:
    return list(
        (
            await db.scalars(
                select(IncidentPlaybook)
                .where(IncidentPlaybook.incident_id == incident.id)
                .order_by(IncidentPlaybook.applied_at)
            )
        ).all()
    )


async def task_finished(db: AsyncSession, incident: Incident, task: Task, actor: User) -> None:
    """A playbook step's task finished: mark the step and open what was waiting on it."""
    if task.run_id is None or task.step is None:
        return
    run = await db.get(IncidentPlaybook, task.run_id)
    if run is None:
        return
    steps = [dict(s) for s in run.steps]
    step = steps[task.step]
    if step["task_id"] != str(task.id):  # an earlier run of a step that has since run again
        return
    step["state"] = DONE
    if step["outcomes"]:
        # A decision finished without one (cancelled): nothing it would have opened happens
        for outcome in step["outcomes"]:
            for p in outcome["opens"]:
                if steps[p]["state"] == WAITING and not steps[p]["armed"]:
                    steps[p]["state"] = SKIPPED
    run.steps = steps
    await advance(db, incident, run, actor)


async def phase_changed(db: AsyncSession, incident: Incident, actor: User) -> None:
    for run in await runs(db, incident):
        await advance(db, incident, run, actor)


def _downstream(steps: list[dict[str, Any]], start: int) -> set[int]:
    """`start` and every step that follows it, directly or not."""
    found, grew = {start}, True
    while grew:
        grew = False
        for s in steps:
            if s["position"] not in found and any(a in found for a in s["after"]):
                found.add(s["position"])
                grew = True
    return found


async def decide(db: AsyncSession, incident: Incident, task: Task, label: str, actor: User) -> dict[str, Any]:
    """Record a decision's outcome and arm the steps it opens. The caller carries out its action, then
    calls `advance`. Returns the outcome."""
    if label not in task.outcomes:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"{task.ref}'s outcomes are: {', '.join(task.outcomes)}."
        )
    run = await db.get(IncidentPlaybook, task.run_id) if task.run_id else None
    steps = [dict(s) for s in run.steps] if run else []
    step = steps[task.step] if run and task.step is not None else None
    outcome = (
        next(
            (o for o in step["outcomes"] if o["label"] == label),
            {"label": label, "action": "none", "opens": []},
        )
        if step
        else {"label": label, "action": "none", "opens": []}
    )
    task.outcome = label
    task.status = TaskStatus.DONE
    task.completed_at, task.completed_by = utcnow(), actor.id
    task.version += 1
    task.updated_at = utcnow()
    if run and step is not None:
        step["state"], step["outcome"] = DONE, label
        chosen = set(outcome["opens"])
        for other in step["outcomes"]:
            for p in other["opens"]:
                if p in chosen:
                    continue
                if steps[p]["state"] == WAITING and not steps[p]["armed"]:
                    steps[p]["state"] = SKIPPED
        for p in chosen:
            if p < step["position"]:
                # Back to an earlier step: it and everything after it run again
                again = _downstream(steps, p)
                for q in again:
                    steps[q].update(state=WAITING, outcome=None)
                # The decisions coming round again can choose differently: what they gate waits again
                for d in again:
                    for o in steps[d]["outcomes"]:
                        for q in o["opens"]:
                            if q > d:
                                steps[q].update(state=WAITING, armed=False)
            elif steps[p]["state"] in (WAITING, SKIPPED):
                steps[p].update(state=WAITING, armed=True)
        run.steps = steps
    return outcome


async def act(db: AsyncSession, incident: Incident, outcome: dict[str, Any], actor: User) -> str:
    """Carry out an outcome's action on the record, except promotion, which the caller does. Returns
    words for the activity log."""
    action = outcome.get("action", "none")
    if action == "close" and incident.status is IncidentStatus.OPEN:
        incident.status, incident.closed_at = IncidentStatus.CLOSED, utcnow()
        incident.version += 1
        return f"closed {incident.ref}"
    if action == "phase" and outcome.get("phase"):
        incident.phase = next(p for p in PHASES_OF[incident.kind] if p.value == outcome["phase"])
        incident.version += 1
        return f"moved {incident.ref} to {outcome['phase']}"
    return ""


def check(steps: list[Any], kind_phases: list[str]) -> None:
    """A playbook's steps make a workflow that can run: `after` names earlier steps only, outcomes
    open steps that exist, and phases belong to the playbook's kind of record."""
    count = len(steps)
    for i, s in enumerate(steps):
        if any(a >= i or a < 0 for a in s.after):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Step {i + 1} can only follow steps before it."
            )
        if s.phase and s.phase not in kind_phases:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Step {i + 1}: “{s.phase}” isn't a phase here ({', '.join(kind_phases)}).",
            )
        for o in s.outcomes:
            if any(p >= count or p < 0 or p == i for p in o.opens):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"Step {i + 1}: an outcome opens a step that isn't there.",
                )
            if o.action == "phase" and o.phase not in kind_phases:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"Step {i + 1}: “{o.label}” moves to a phase that isn't here.",
                )
