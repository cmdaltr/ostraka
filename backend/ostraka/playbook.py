"""Playbooks on the attack's anatomy: the parts a playbook covers, and the tasks applying them makes.

A playbook's checks hang on the parts of an attack (`ostraka.flow.STAGES`: the ways in, the toolkit, the
actions and where it goes) or on none ("any": people to tell, approvals, notices). Applying it to a record
takes the parts that fit: each check of a part taken becomes a task on that part of the record's attack
flow, in its team's queue, and the team is told. A part with an "only if" starts untaken, so whoever
applies the playbook makes that call; a part left out can be taken later, and each part is taken once.
There are no decisions, waits or phases: within a part, the order of the checks is the order of the work.

Everything here is written with the caller's commit.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import flow, notify, work
from ostraka.incidents import activity
from ostraka.models import (
    Incident,
    IncidentPlaybook,
    IncidentTeam,
    NotificationKind,
    Playbook,
    Task,
    Team,
    User,
)

ANY = "any"
PARTS: tuple[str, ...] = (*(s.key for s in flow.STAGES), ANY)
NAMES: dict[str, str] = {s.key: s.name for s in flow.STAGES} | {ANY: "Not tied to a step"}


def parts_of(playbook: Playbook) -> list[str]:
    """The parts the playbook has checks in, in the anatomy's order."""
    present = {c.part for c in playbook.checks}
    return [p for p in PARTS if p in present]


def default_parts(playbook: Playbook) -> list[str]:
    """What applying takes unless told otherwise: every part with checks and no "only if"."""
    return [p for p in parts_of(playbook) if not playbook.conditions.get(p)]


def check_parts(parts: Iterable[str]) -> None:
    """Refuse a part the anatomy doesn't have."""
    unknown = sorted(set(parts) - set(PARTS))
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"No part of the attack called {unknown[0]!r}."
        )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


async def _team(db: AsyncSession, incident: Incident, team_id: uuid.UUID | None, actor: User) -> Team | None:
    """The check's team, brought into the record by the playbook if it isn't yet (None if it has gone)."""
    if team_id is None:
        return None
    team = await db.get(Team, team_id)
    if team is None:
        return None
    if not any(t.team_id == team_id for t in incident.teams):
        incident.teams.append(IncidentTeam(team_id=team_id))
        await activity.record(
            db,
            incident,
            actor,
            "team_added",
            f"A playbook brought in {team.name}",
            entity_type="team",
            entity_id=team_id,
        )
    return team


async def apply(
    db: AsyncSession, incident: Incident, playbook: Playbook, parts: list[str] | None, actor: User
) -> list[Task]:
    """Take these parts of the playbook (its default parts if None) on the record: a task for each of their
    checks, in order. A part taken before isn't taken again. Checks without a team go to the person
    applying it. Returns the tasks made."""
    if not playbook.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{playbook.name} is retired.")
    if playbook.applies_to is not incident.kind:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{playbook.name} is for {playbook.applies_to.value}s, not {incident.kind.value}s.",
        )
    wanted = default_parts(playbook) if parts is None else list(dict.fromkeys(parts))
    check_parts(wanted)
    missing = [p for p in wanted if p not in parts_of(playbook)]
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{playbook.name} has no checks for {NAMES[missing[0]]}."
        )
    run = await db.scalar(
        select(IncidentPlaybook).where(
            IncidentPlaybook.incident_id == incident.id, IncidentPlaybook.playbook_id == playbook.id
        )
    )
    taken = set(run.parts) if run else set()
    new = [p for p in PARTS if p in wanted and p not in taken]
    if not new:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{playbook.name} is already applied to {incident.ref}"
            + (" with those parts." if taken else "; choose at least one part."),
        )
    if run is None:
        run = IncidentPlaybook(
            incident_id=incident.id, playbook_id=playbook.id, applied_by=actor.id, parts=[]
        )
        db.add(run)
        await db.flush()
    run.parts = [p for p in PARTS if p in taken or p in new]  # a new list, so the column is written

    made: list[Task] = []
    for check in sorted(
        (c for c in playbook.checks if c.part in new), key=lambda c: (PARTS.index(c.part), c.position)
    ):
        team = await _team(db, incident, check.team_id, actor)
        task = Task(
            incident_id=incident.id,
            title=check.title,
            team_id=team.id if team else None,
            assignee_id=None if team else actor.id,
            stage=None if check.part == ANY else check.part,
            playbook_id=playbook.id,
            run_id=run.id,
            created_by=actor.id,
            checklist=[],
        )
        db.add(task)
        made.append(task)
    await db.flush()
    for task in made:
        await db.refresh(task, ["team", "assignee"])

    named = ", ".join(NAMES[p] for p in new if p != ANY) or "no part of the attack"
    extra = (
        f", and {_plural(sum(1 for t in made if t.stage is None), 'check')} not tied to a step"
        if ANY in new
        else ""
    )
    await activity.record(
        db,
        incident,
        actor,
        "playbook_applied",
        f"{actor.name} applied {playbook.name} ({named}{extra}): {_plural(len(made), 'task')}",
        entity_type="task",
        playbook_id=playbook.id,
    )
    await _tell(db, incident, playbook, made, actor)
    return made


async def _tell(
    db: AsyncSession, incident: Incident, playbook: Playbook, made: list[Task], actor: User
) -> None:
    """One notice per team for the tasks it now has. A task for a person is announced to them."""
    by_team: dict[uuid.UUID, list[Task]] = defaultdict(list)
    for task in made:
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
            else f"{len(ready)} checks of {playbook.name} ready for {team.name}"
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
