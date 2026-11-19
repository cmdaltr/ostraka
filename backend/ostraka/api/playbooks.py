"""Playbooks: checks on the attack's anatomy. Anyone can read them; admins and team leads write them.
Applying one to an incident or hunt takes the parts that fit (`ostraka.playbook`); more can be taken
later."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit, work
from ostraka import playbook as playbooks
from ostraka.auth import policy, sessions
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incident_schemas import TeamRef
from ostraka.incidents import access, activity, views
from ostraka.models import (
    Incident,
    IncidentPlaybook,
    Playbook,
    PlaybookCheck,
    Task,
    Team,
    User,
    utcnow,
)
from ostraka.work_schemas import (
    AppliedPlaybookOut,
    ApplyIn,
    PlaybookCheckIn,
    PlaybookCheckOut,
    PlaybookIn,
    PlaybookOut,
    PlaybookPatch,
    TaskOut,
)

router = APIRouter(prefix="/api/playbooks", tags=["playbooks"])
incident_router = APIRouter(prefix="/api/incidents/{incident_id}/playbooks", tags=["playbooks"])


async def _out(db: AsyncSession, playbook: Playbook) -> PlaybookOut:
    team_ids = {c.team_id for c in playbook.checks if c.team_id}
    teams = (
        {t.id: t for t in (await db.scalars(select(Team).where(Team.id.in_(team_ids)))).all()}
        if team_ids
        else {}
    )
    return PlaybookOut(
        id=playbook.id,
        name=playbook.name,
        description=playbook.description,
        trigger_tags=playbook.trigger_tags,
        applies_to=playbook.applies_to,
        is_active=playbook.is_active,
        conditions=playbook.conditions,
        checks=[
            PlaybookCheckOut(
                id=c.id,
                part=c.part,
                title=c.title,
                team=TeamRef(id=c.team_id, name=teams[c.team_id].name) if c.team_id in teams else None,
            )
            for c in playbook.checks
        ],
        updated_at=playbook.updated_at,
        version=playbook.version,
    )


def _require_author(user: User) -> None:
    if not policy.can_manage_playbooks(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins and team leads can change playbooks.")


async def _checks(db: AsyncSession, checks: list[PlaybookCheckIn]) -> list[PlaybookCheck]:
    playbooks.check_parts(c.part for c in checks)
    team_ids = {c.team_id for c in checks if c.team_id}
    if team_ids:
        found = set((await db.scalars(select(Team.id).where(Team.id.in_(team_ids)))).all())
        if team_ids - found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "A check names a team that doesn't exist.")
    return [
        PlaybookCheck(position=i, part=c.part, title=c.title.strip(), team_id=c.team_id)
        for i, c in enumerate(checks)
    ]


def _conditions(
    conditions: dict[str, str], checks: list[PlaybookCheckIn] | list[PlaybookCheck]
) -> dict[str, str]:
    """Only if…, for parts that have checks. "Not tied to a step" never has one: it's never a call."""
    playbooks.check_parts(conditions)
    present = {c.part for c in checks}
    return {p: w for p, w in conditions.items() if p in present and p != playbooks.ANY}


async def _save(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A playbook with that name already exists.") from exc


async def _playbook(db: AsyncSession, playbook_id: uuid.UUID) -> Playbook:
    playbook = await db.get(Playbook, playbook_id)
    if playbook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such playbook.")
    return playbook


@router.get("", response_model=list[PlaybookOut])
async def list_playbooks(
    _: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[PlaybookOut]:
    playbooks = (await db.scalars(select(Playbook).order_by(Playbook.name))).all()
    return [await _out(db, p) for p in playbooks]


@router.post("", response_model=PlaybookOut, status_code=status.HTTP_201_CREATED)
async def create_playbook(
    body: PlaybookIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> PlaybookOut:
    _require_author(user)
    playbook = Playbook(
        id=uuid.uuid4(),  # now, for the security log; a flush here would hide a duplicate name's 409
        name=body.name.strip(),
        description=body.description.strip(),
        trigger_tags=body.trigger_tags,
        applies_to=body.applies_to,
        is_active=body.is_active,
        conditions=_conditions(body.conditions, body.checks),
        created_by=user.id,
        checks=await _checks(db, body.checks),
    )
    db.add(playbook)
    audit.record(
        db,
        "playbook_added",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        playbook=str(playbook.id),
        name=playbook.name,
        checks=len(body.checks),
    )
    await _save(db)
    await db.refresh(playbook, ["checks"])
    return await _out(db, playbook)


@router.patch("/{playbook_id}", response_model=PlaybookOut)
async def update_playbook(
    playbook_id: uuid.UUID,
    body: PlaybookPatch,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> PlaybookOut:
    """Changes apply to incidents it's applied to from now on; tasks it already made stay as they are."""
    _require_author(user)
    playbook = await _playbook(db, playbook_id)
    views.check_version(playbook.version, body.version, "playbook")
    changes = body.model_dump(exclude_unset=True, exclude={"version", "checks", "conditions"})
    for field, value in changes.items():
        if value is not None:
            setattr(playbook, field, value.strip() if isinstance(value, str) else value)
    if body.checks is not None:
        playbook.checks = await _checks(db, body.checks)
    if body.conditions is not None or body.checks is not None:
        conditions = playbook.conditions if body.conditions is None else body.conditions
        playbook.conditions = _conditions(
            conditions, body.checks if body.checks is not None else playbook.checks
        )
    playbook.version += 1
    playbook.updated_at = utcnow()
    audit.record(
        db,
        "playbook_changed",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        playbook=str(playbook.id),
        name=playbook.name,
        fields=sorted(
            [
                *changes,
                *(["checks"] if body.checks is not None else []),
                *(["conditions"] if body.conditions is not None else []),
            ]
        ),
    )
    await _save(db)
    await db.refresh(playbook, ["checks"])
    return await _out(db, playbook)


@router.delete("/{playbook_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_playbook(
    playbook_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    _require_author(user)
    playbook = await _playbook(db, playbook_id)
    audit.record(
        db,
        "playbook_removed",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        playbook=str(playbook.id),
        name=playbook.name,
    )
    await db.delete(playbook)
    await db.commit()


async def apply(
    db: AsyncSession, incident: Incident, playbook: Playbook, user: User, parts: list[str] | None = None
) -> list[Task]:
    """Take the parts on the record (`ostraka.playbook.apply`). Written with the caller's commit."""
    return await playbooks.apply(db, incident, playbook, parts, user)


@incident_router.get("", response_model=list[AppliedPlaybookOut])
async def applied(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[AppliedPlaybookOut]:
    incident = await access.load(db, user, incident_id)
    rows = (
        await db.execute(
            select(IncidentPlaybook, Playbook)
            .join(Playbook, Playbook.id == IncidentPlaybook.playbook_id)
            .where(IncidentPlaybook.incident_id == incident.id)
            .order_by(IncidentPlaybook.applied_at)
        )
    ).all()
    lookup = await views.names(db, [r.applied_by for r, _ in rows])
    return [
        AppliedPlaybookOut(
            playbook_id=r.playbook_id,
            name=pb.name,
            applied_by=views.person(lookup, r.applied_by),
            applied_at=r.applied_at,
            parts=r.parts,
            remaining=[p for p in playbooks.parts_of(pb) if p not in r.parts],
            conditions=pb.conditions,
        )
        for r, pb in rows
    ]


@incident_router.post("/{playbook_id}", response_model=list[TaskOut], status_code=status.HTTP_201_CREATED)
async def apply_playbook(
    incident_id: uuid.UUID,
    playbook_id: uuid.UUID,
    body: ApplyIn | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[TaskOut]:
    """Take parts of the playbook on the record: those named, or every part without an only if."""
    incident = await access.load(db, user, incident_id, edit=True)
    tasks = await apply(db, incident, await _playbook(db, playbook_id), user, body.parts if body else None)
    await activity.finish(db, user)
    return [await work.task_out(db, t, user) for t in tasks]
