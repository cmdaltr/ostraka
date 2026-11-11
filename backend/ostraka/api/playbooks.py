"""Playbooks: the workflow a record follows. Anyone can read them; admins and team leads write them.
Applying one to an incident or hunt starts it there, once; `ostraka.workflow` opens each step's task
when it's ready."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import work, workflow
from ostraka.auth import policy
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incident_schemas import TeamRef
from ostraka.incidents import access, activity, views
from ostraka.models import (
    PHASES_OF,
    Incident,
    IncidentPlaybook,
    Playbook,
    PlaybookStep,
    RecordKind,
    Task,
    Team,
    User,
    utcnow,
)
from ostraka.work_schemas import (
    AppliedPlaybookOut,
    OutcomeIn,
    PlaybookIn,
    PlaybookOut,
    PlaybookPatch,
    PlaybookStepIn,
    PlaybookStepOut,
    TaskOut,
    WorkflowStepOut,
)

router = APIRouter(prefix="/api/playbooks", tags=["playbooks"])
incident_router = APIRouter(prefix="/api/incidents/{incident_id}/playbooks", tags=["playbooks"])


async def _out(db: AsyncSession, playbook: Playbook) -> PlaybookOut:
    team_ids = {s.team_id for s in playbook.steps if s.team_id}
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
        steps=[
            PlaybookStepOut(
                id=s.id,
                title=s.title,
                description=s.description,
                team=TeamRef(id=s.team_id, name=teams[s.team_id].name) if s.team_id in teams else None,
                due_minutes=s.due_minutes,
                checklist=s.checklist,
                after=s.after,
                phase=s.phase,
                outcomes=[OutcomeIn(**o) for o in s.outcomes],
            )
            for s in playbook.steps
        ],
        updated_at=playbook.updated_at,
        version=playbook.version,
    )


def _require_author(user: User) -> None:
    if not policy.can_manage_playbooks(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins and team leads can change playbooks.")


async def _steps(db: AsyncSession, steps: list[PlaybookStepIn], kind: RecordKind) -> list[PlaybookStep]:
    workflow.check(steps, [p.value for p in PHASES_OF[kind]])
    team_ids = {s.team_id for s in steps if s.team_id}
    if team_ids:
        found = set((await db.scalars(select(Team.id).where(Team.id.in_(team_ids)))).all())
        if team_ids - found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "A step names a team that doesn't exist.")
    return [
        PlaybookStep(
            position=i,
            title=s.title.strip(),
            description=s.description.strip(),
            team_id=s.team_id,
            due_minutes=s.due_minutes,
            checklist=s.checklist,
            after=sorted(set(s.after)),
            phase=s.phase,
            outcomes=[o.model_dump() for o in s.outcomes],
        )
        for i, s in enumerate(steps)
    ]


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
    body: PlaybookIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> PlaybookOut:
    _require_author(user)
    playbook = Playbook(
        name=body.name.strip(),
        description=body.description.strip(),
        trigger_tags=body.trigger_tags,
        applies_to=body.applies_to,
        is_active=body.is_active,
        created_by=user.id,
        steps=await _steps(db, body.steps, body.applies_to),
    )
    db.add(playbook)
    await _save(db)
    await db.refresh(playbook, ["steps"])
    return await _out(db, playbook)


@router.patch("/{playbook_id}", response_model=PlaybookOut)
async def update_playbook(
    playbook_id: uuid.UUID,
    body: PlaybookPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> PlaybookOut:
    """Changes apply to incidents it's applied to from now on; tasks it already made stay as they are."""
    _require_author(user)
    playbook = await _playbook(db, playbook_id)
    views.check_version(playbook.version, body.version, "playbook")
    changes = body.model_dump(exclude_unset=True, exclude={"version", "steps"})
    for field, value in changes.items():
        if value is not None:
            setattr(playbook, field, value.strip() if isinstance(value, str) else value)
    if body.steps is not None:
        playbook.steps = await _steps(db, body.steps, body.applies_to or playbook.applies_to)
    playbook.version += 1
    playbook.updated_at = utcnow()
    await _save(db)
    await db.refresh(playbook, ["steps"])
    return await _out(db, playbook)


@router.delete("/{playbook_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_playbook(
    playbook_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> None:
    _require_author(user)
    await db.delete(await _playbook(db, playbook_id))
    await db.commit()


async def apply(db: AsyncSession, incident: Incident, playbook: Playbook, user: User) -> list[Task]:
    """Start the playbook on the record and open the steps that are ready now. Steps without a team go
    to whoever applies it. Written with the caller's commit."""
    if not playbook.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{playbook.name} is retired.")
    if playbook.applies_to is not incident.kind:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{playbook.name} is for {playbook.applies_to.value}s, not {incident.kind.value}s.",
        )
    already = await db.scalar(
        select(IncidentPlaybook.id).where(
            IncidentPlaybook.incident_id == incident.id, IncidentPlaybook.playbook_id == playbook.id
        )
    )
    if already:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{playbook.name} is already applied to {incident.ref}."
        )
    run = IncidentPlaybook(
        incident_id=incident.id, playbook_id=playbook.id, applied_by=user.id, steps=workflow.plan(playbook)
    )
    db.add(run)
    await db.flush()
    await activity.record(
        db,
        incident,
        user,
        "playbook_applied",
        f"{user.name} applied {playbook.name}: {len(playbook.steps)} "
        f"step{'' if len(playbook.steps) == 1 else 's'}",
        entity_type="task",
        playbook_id=playbook.id,
    )
    return await workflow.advance(db, incident, run, user)


@incident_router.get("", response_model=list[AppliedPlaybookOut])
async def applied(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[AppliedPlaybookOut]:
    incident = await access.load(db, user, incident_id)
    rows = (
        await db.execute(
            select(IncidentPlaybook, Playbook.name)
            .join(Playbook, Playbook.id == IncidentPlaybook.playbook_id)
            .where(IncidentPlaybook.incident_id == incident.id)
            .order_by(IncidentPlaybook.applied_at)
        )
    ).all()
    lookup = await views.names(db, [r.applied_by for r, _ in rows])
    team_ids = {uuid.UUID(s["team_id"]) for r, _ in rows for s in r.steps if s["team_id"]}
    teams = {str(t.id): t for t in (await db.scalars(select(Team).where(Team.id.in_(team_ids)))).all()}
    task_ids = {uuid.UUID(s["task_id"]) for r, _ in rows for s in r.steps if s["task_id"]}
    refs = {str(t.id): t.ref for t in (await db.scalars(select(Task).where(Task.id.in_(task_ids)))).all()}
    out = []
    for r, name in rows:
        conditional = workflow.gated(r.steps)
        steps = [
            WorkflowStepOut(
                position=s["position"],
                title=s["title"],
                team=TeamRef(id=teams[s["team_id"]].id, name=teams[s["team_id"]].name)
                if s["team_id"] in teams
                else None,
                after=s["after"],
                phase=s["phase"],
                outcomes=[OutcomeIn(**o) for o in s["outcomes"]],
                state=s["state"],
                conditional=s["position"] in conditional,
                task_id=s["task_id"],
                task_ref=refs.get(s["task_id"]),
                outcome=s["outcome"],
                runs=s["runs"],
            )
            for s in r.steps
        ]
        out.append(
            AppliedPlaybookOut(
                playbook_id=r.playbook_id,
                name=name,
                applied_by=views.person(lookup, r.applied_by),
                applied_at=r.applied_at,
                steps=steps,
            )
        )
    return out


@incident_router.post("/{playbook_id}", response_model=list[TaskOut], status_code=status.HTTP_201_CREATED)
async def apply_playbook(
    incident_id: uuid.UUID,
    playbook_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[TaskOut]:
    incident = await access.load(db, user, incident_id, edit=True)
    tasks = await apply(db, incident, await _playbook(db, playbook_id), user)
    await activity.finish(db, user)
    return [await work.task_out(db, t, user) for t in tasks]
