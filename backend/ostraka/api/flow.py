"""The attack flow (`ostraka/flow.py`): reading it, an analyst's call on a stage, and attribution to an
ATT&CK group."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import flow
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer
from ostraka.models import FlowStage, StageCall, User, utcnow
from ostraka.reports import attack

router = APIRouter(prefix="/api/incidents/{incident_id}/flow", tags=["flow"])
groups_router = APIRouter(prefix="/api/attack", tags=["flow"])

CALL_WORDS = {
    StageCall.EXPECTED: "expects",
    StageCall.DISMISSED: "ruled out",
    StageCall.CLEARED: "checked and found nothing in",
}


class TechniqueOut(BaseModel):
    id: str
    name: str


class SuggestionOut(TechniqueOut):
    groups: int


class FindingOut(BaseModel):
    ref: str
    title: str
    status: str


class LinkOut(BaseModel):
    source: str
    target: str
    technique: str


class TaskOut(BaseModel):
    ref: str
    title: str


class CallOut(BaseModel):
    call: StageCall
    note: str
    by: str
    at: datetime


class StageOut(BaseModel):
    key: str
    name: str
    says: str
    layer: str  # seen, expected or added
    coverage: str  # covered, focused or open
    why: str
    findings: list[FindingOut]
    links: list[LinkOut]
    techniques: list[TechniqueOut]
    suggestions: list[SuggestionOut]
    tasks_open: list[TaskOut]
    tasks_done: int
    call: CallOut | None


class DismissedOut(BaseModel):
    key: str
    name: str
    note: str
    by: str
    at: datetime


class GroupOut(BaseModel):
    id: str
    name: str


class FlowOut(BaseModel):
    stages: list[StageOut]
    dismissed: list[DismissedOut]
    covered: int
    peers: int
    group: GroupOut | None
    all_stages: list[GroupOut]  # every stage, for adding one or giving a task one
    can_edit: bool


class CallIn(BaseModel):
    call: StageCall
    note: str = Field(min_length=3, max_length=2000)


class AttributionIn(BaseModel):
    group: str | None = Field(default=None, max_length=16)


def _stage(key: str) -> flow.Stage:
    if key not in flow.BY_KEY:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No attack-flow stage is called {key}.")
    return flow.BY_KEY[key]


@router.get("", response_model=FlowOut)
async def read_flow(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> FlowOut:
    """The flow as the record stands, masked for the viewer."""
    incident = await access.load(db, user, incident_id)
    deciders = (await db.scalars(select(FlowStage.by).where(FlowStage.incident_id == incident.id))).all()
    names = await views.names(db, [d for d in deciders if d])
    built = flow.build(await flow.gather(db, incident, names))
    seen = lambda text: for_viewer(user, text)  # noqa: E731
    return FlowOut(
        stages=[
            StageOut(
                key=s.key,
                name=s.name,
                says=s.says,
                layer=s.layer,
                coverage=s.coverage,
                why=seen(s.why),
                findings=[
                    FindingOut(ref=f.ref, title=seen(f.title), status=f.status.value) for f in s.findings
                ],
                links=[
                    LinkOut(source=seen(x.source), target=seen(x.target), technique=x.technique)
                    for x in s.links
                ],
                techniques=[TechniqueOut(id=t, name=n) for t, n in s.techniques],
                suggestions=[
                    SuggestionOut(id=x.technique, name=x.name, groups=x.groups) for x in s.suggestions
                ],
                tasks_open=[TaskOut(ref=t.ref, title=seen(t.title)) for t in s.tasks_open],
                tasks_done=s.tasks_done,
                call=CallOut(call=s.call.call, note=seen(s.call.note), by=s.call.by, at=s.call.at)
                if s.call
                else None,
            )
            for s in built.stages
        ],
        dismissed=[
            DismissedOut(key=s.key, name=s.name, note=seen(c.note), by=c.by, at=c.at)
            for s, c in built.dismissed
        ],
        covered=built.covered,
        peers=built.peers,
        group=GroupOut(id=built.group[0], name=built.group[1]) if built.group else None,
        all_stages=[GroupOut(id=s.key, name=s.name) for s in flow.STAGES],
        can_edit=access.can_edit(user, incident),
    )


@router.put("/stages/{stage}", status_code=status.HTTP_204_NO_CONTENT)
async def call_stage(
    incident_id: uuid.UUID,
    stage: str,
    body: CallIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """An analyst's call on a stage: expected, ruled out, or checked and nothing found. The note says why,
    or where they looked."""
    incident = await access.load(db, user, incident_id, edit=True)
    named = _stage(stage)
    row = await db.scalar(
        select(FlowStage).where(FlowStage.incident_id == incident.id, FlowStage.stage == named.key)
    )
    if row is None:
        row = FlowStage(incident_id=incident.id, stage=named.key, call=body.call)
        db.add(row)
    row.call, row.note, row.by, row.at = body.call, body.note.strip(), user.id, utcnow()
    await db.flush()
    await activity.record(
        db,
        incident,
        user,
        "flow_stage",
        f"{user.name} {CALL_WORDS[body.call]} {named.name}: {row.note}",
        entity_type="flow",
        entity_id=row.id,
        stage=named.key,
        call=body.call.value,
    )
    await activity.finish(db, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/stages/{stage}", status_code=status.HTTP_204_NO_CONTENT)
async def uncall_stage(
    incident_id: uuid.UUID,
    stage: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Withdraw a call on a stage, so it's read from the record again."""
    incident = await access.load(db, user, incident_id, edit=True)
    named = _stage(stage)
    row = await db.scalar(
        select(FlowStage).where(FlowStage.incident_id == incident.id, FlowStage.stage == named.key)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nobody has made a call on {named.name}.")
    await db.delete(row)
    await activity.record(
        db,
        incident,
        user,
        "flow_stage",
        f"{user.name} withdrew the call on {named.name}",
        entity_type="flow",
        stage=named.key,
        call="withdrawn",
    )
    await activity.finish(db, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/attribution", status_code=status.HTTP_204_NO_CONTENT)
async def attribute(
    incident_id: uuid.UUID,
    body: AttributionIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Attribute the record to an ATT&CK group, or withdraw the attribution. The flow's expectations then
    come from that group's techniques."""
    incident = await access.load(db, user, incident_id, edit=True)
    known = attack.groups()
    group = (body.group or "").strip().upper() or None
    if group is not None and group not in known:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{group} isn't an ATT&CK group.")
    if group == incident.attributed_group:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    incident.attributed_group = group
    summary = (
        f"{user.name} attributed {incident.ref} to {known[group][0]} ({group})"
        if group
        else f"{user.name} withdrew the attribution of {incident.ref}"
    )
    await activity.record(db, incident, user, "attributed", summary, group=group or "")
    await activity.finish(db, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@groups_router.get("/groups", response_model=list[GroupOut])
async def list_groups(user: User = Depends(current_user)) -> list[GroupOut]:
    """Every ATT&CK group, for attributing a record."""
    return [
        GroupOut(id=gid, name=name)
        for gid, (name, _) in sorted(attack.groups().items(), key=lambda g: g[1][0])
    ]
