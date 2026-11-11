"""Detection gaps (`ostraka.integrations.gaps`): per incident, and across every incident you can see.

Asking Shikari again is open to anyone who can work on the incident, on every edition. It also
opens gaps for confirmed techniques not yet tracked (confirmed before Shikari was connected, or
added to a finding since). A gap without a task (a restricted incident's, or one made before a
detection team was chosen) can be given one here, by the usual rules for giving tasks.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import licence
from ostraka.api.tasks import create_task
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incidents import access, activity, views
from ostraka.integration_schemas import GapIncident, GapOut, GapsOut, GapTask, GapTaskIn
from ostraka.integrations import configured, gaps
from ostraka.integrations.shikari import Shikari
from ostraka.models import FINISHED, DetectionGap, Finding, GapStatus, Incident, Product, Task, User
from ostraka.work_schemas import TaskIn

router = APIRouter(prefix="/api", tags=["detection gaps"])


async def _gap_out(
    db: AsyncSession, rows: list[DetectionGap], *, with_incident: bool = False
) -> list[GapOut]:
    lookup = await views.names(db, [g.opened_by for g in rows])
    out = []
    for g in rows:
        finding = await db.get(Finding, g.finding_id) if g.finding_id else None
        task = await db.get(Task, g.task_id) if g.task_id else None
        incident = await db.get(Incident, g.incident_id) if with_incident else None
        out.append(
            GapOut(
                id=g.id,
                technique=g.technique,
                technique_name=g.technique_name,
                attack_url=gaps.attack_url(g.technique),
                status=g.status,
                finding_id=g.finding_id,
                finding_ref=f"FND-{finding.seq}" if finding else None,
                task=GapTask(id=task.id, ref=task.ref, title=task.title, status=task.status)
                if task
                else None,
                opened_by=views.person(lookup, g.opened_by),
                opened_at=g.opened_at,
                checked_at=g.checked_at,
                check_error=g.check_error,
                closed_at=g.closed_at,
                closed_by=g.closed_by,
                incident=GapIncident(
                    id=incident.id,
                    ref=incident.ref,
                    title=incident.title,
                    severity=incident.severity,
                    tlp=incident.tlp,
                )
                if incident
                else None,
            )
        )
    return out


async def _gaps_out(db: AsyncSession, incident: Incident, message: str = "") -> GapsOut:
    row = await configured(db, Product.SHIKARI)
    rows = (
        await db.scalars(
            select(DetectionGap)
            .where(DetectionGap.incident_id == incident.id)
            .order_by(DetectionGap.status, DetectionGap.technique)
        )
    ).all()
    return GapsOut(
        configured=row is not None,
        automation=(await licence.current(db)).has(licence.AUTOMATION),
        coverage_url=Shikari.of(row).coverage_link() if row else None,
        message=message,
        gaps=await _gap_out(db, list(rows)),
    )


@router.get("/incidents/{incident_id}/detection-gaps", response_model=GapsOut)
async def incident_gaps(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> GapsOut:
    incident = await access.load(db, user, incident_id)
    return await _gaps_out(db, incident)


@router.post("/incidents/{incident_id}/detection-gaps/check", response_model=GapsOut)
async def check_gaps(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> GapsOut:
    """Ask Shikari now: open gaps for confirmed techniques not yet tracked, and close any gap Shikari
    has a detection for."""
    incident = await access.load(db, user, incident_id, edit=True)
    if await configured(db, Product.SHIKARI) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No Shikari server is connected.")
    opened = await gaps.catch_up(db, incident, user)
    open_rows = (
        await db.scalars(
            select(DetectionGap).where(
                DetectionGap.incident_id == incident.id,
                DetectionGap.status == GapStatus.OPEN,
            )
        )
    ).all()
    result = await gaps.recheck(db, list(open_rows), f"Checked with Shikari by {user.name}")
    await activity.finish(db, user)
    if result.error or (opened and opened.error):
        message = result.error or opened.error
    elif result.closed:
        message = f"Shikari has detections for {result.closed} of them: closed."
    else:
        message = "Shikari has no new detections for these techniques."
    return await _gaps_out(db, incident, message)


@router.post("/incidents/{incident_id}/detection-gaps/{gap_id}/task", response_model=GapsOut)
async def give_gap_task(
    incident_id: uuid.UUID,
    gap_id: uuid.UUID,
    body: GapTaskIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> GapsOut:
    """Give an open gap a task, when it has none (or its task was cancelled)."""
    incident = await access.load(db, user, incident_id, edit=True)
    gap = await db.get(DetectionGap, gap_id)
    if gap is None or gap.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That detection gap isn't in this incident.")
    if gap.status is not GapStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, "Shikari has a detection for that already.")
    current = await db.get(Task, gap.task_id) if gap.task_id else None
    if current is not None and current.status not in FINISHED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{current.ref} is already following it up.")
    name = f" ({gap.technique_name})" if gap.technique_name else ""
    task = await create_task(
        db,
        incident,
        user,
        TaskIn(
            title=f"Detection for {gap.technique}{name}"[:200],
            description=(
                f"Shikari has no detection for {gap.technique}. Build one in Shikari: the gap closes when "
                f"Shikari has it.\n\n{gaps.attack_url(gap.technique)}"
            ),
            team_id=body.team_id,
            assignee_id=body.assignee_id,
        ),
        origin=f"for the detection gap {gap.technique}",
    )
    gap.task_id = task.id
    await activity.finish(db, user)
    return await _gaps_out(db, incident)


@router.get("/detection-gaps", response_model=list[GapOut])
async def all_gaps(
    state: str = Query(default="open", alias="status", pattern="^(open|closed|all)$"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[GapOut]:
    """Detection gaps in every incident you can see, open ones by default, newest first."""
    query = (
        select(DetectionGap)
        .join(Incident, Incident.id == DetectionGap.incident_id)
        .where(access.visible_filter(user))
        .order_by(DetectionGap.opened_at.desc())
        .limit(500)
    )
    if state != "all":
        query = query.where(DetectionGap.status == GapStatus(state))
    return await _gap_out(db, list((await db.scalars(query)).all()), with_incident=True)
