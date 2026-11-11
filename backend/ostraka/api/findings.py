"""Findings: claims about the incident, each a lead until verified and confirmed, or refuted."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incident_schemas import DecisionIn, FindingIn, FindingOut, FindingPatch, VerificationIn
from ostraka.incidents import access, activity, views
from ostraka.models import (
    Asset,
    Finding,
    FindingObservable,
    FindingStatus,
    Incident,
    IncidentObservable,
    User,
    Verification,
    utcnow,
)

router = APIRouter(prefix="/api/incidents/{incident_id}/findings", tags=["findings"])


async def _finding(db: AsyncSession, incident: Incident, finding_id: uuid.UUID) -> Finding:
    finding = await db.get(Finding, finding_id)
    if finding is None or finding.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That finding isn't in this incident.")
    return finding


async def _check_links(db: AsyncSession, incident: Incident, asset_id, observable_ids) -> None:
    if asset_id is not None:
        asset = await db.get(Asset, asset_id)
        if asset is None or asset.incident_id != incident.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That asset isn't in this incident.")
    if observable_ids:
        found = set(
            (
                await db.scalars(
                    select(IncidentObservable.id).where(
                        IncidentObservable.id.in_(observable_ids),
                        IncidentObservable.incident_id == incident.id,
                    )
                )
            ).all()
        )
        if missing := set(observable_ids) - found:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"{len(missing)} of those observables aren't in this incident."
            )


@router.get("", response_model=list[FindingOut])
async def list_findings(
    incident_id: uuid.UUID,
    state: FindingStatus | None = Query(default=None, alias="status"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    await access.load(db, user, incident_id)
    query = select(Finding).where(Finding.incident_id == incident_id).order_by(Finding.created_at.desc())
    if state is not None:
        query = query.where(Finding.status == state)
    return [await views.finding_out(db, user, f) for f in (await db.scalars(query)).all()]


@router.get("/{finding_id}", response_model=FindingOut)
async def get_finding(
    incident_id: uuid.UUID,
    finding_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> FindingOut:
    incident = await access.load(db, user, incident_id)
    return await views.finding_out(db, user, await _finding(db, incident, finding_id))


@router.post("", response_model=FindingOut, status_code=status.HTTP_201_CREATED)
async def add_finding(
    incident_id: uuid.UUID,
    body: FindingIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> FindingOut:
    """Record a finding. It's a lead until someone attaches a verification and confirms it."""
    incident = await access.load(db, user, incident_id, edit=True)
    await _check_links(db, incident, body.asset_id, body.observable_ids)
    finding = Finding(
        incident_id=incident.id,
        created_by=user.id,
        **body.model_dump(exclude={"observable_ids"}),
    )
    finding.observable_links = [FindingObservable(incident_observable_id=i) for i in body.observable_ids]
    db.add(finding)
    await db.flush()
    await activity.record(
        db,
        incident,
        user,
        "finding_added",
        f"{user.name} recorded {finding.kind.value} FND-{finding.seq}: {finding.title}",
        entity_type="finding",
        entity_id=finding.id,
    )
    await activity.finish(db, user)
    await db.refresh(finding, ["verifications", "observable_links"])
    return await views.finding_out(db, user, finding)


@router.patch("/{finding_id}", response_model=FindingOut)
async def update_finding(
    incident_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: FindingPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> FindingOut:
    incident = await access.load(db, user, incident_id, edit=True)
    finding = await _finding(db, incident, finding_id)
    views.check_version(finding.version, body.version, "finding")
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    await _check_links(db, incident, changes.get("asset_id"), changes.get("observable_ids"))
    if "observable_ids" in changes:
        ids = changes.pop("observable_ids") or []
        finding.observable_links = [FindingObservable(incident_observable_id=i) for i in ids]
    for field, value in changes.items():
        setattr(finding, field, value)
    finding.version += 1
    await activity.record(
        db,
        incident,
        user,
        "finding_updated",
        f"{user.name} edited FND-{finding.seq}",
        entity_type="finding",
        entity_id=finding.id,
        fields=sorted(changes),
    )
    await activity.finish(db, user)
    await db.refresh(finding, ["verifications", "observable_links"])
    return await views.finding_out(db, user, finding)


@router.post("/{finding_id}/verifications", response_model=FindingOut, status_code=status.HTTP_201_CREATED)
async def add_verification(
    incident_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: VerificationIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> FindingOut:
    """Attach the check behind a finding: what was searched, where, over what window, with what result."""
    incident = await access.load(db, user, incident_id, edit=True)
    finding = await _finding(db, incident, finding_id)
    db.add(Verification(finding_id=finding.id, ran_by=user.id, **body.model_dump()))
    what = f"{body.platform} search" if body.method.value == "search" else body.method.value
    await activity.record(
        db,
        incident,
        user,
        "verification_added",
        f"{user.name} attached a {what} over {body.scope} to FND-{finding.seq}",
        entity_type="finding",
        entity_id=finding.id,
        results=body.result_count,
    )
    await activity.finish(db, user)
    await db.refresh(finding, ["verifications"])
    return await views.finding_out(db, user, finding)


async def _decide(
    db: AsyncSession,
    user: User,
    incident_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: DecisionIn,
    decision: FindingStatus,
) -> FindingOut:
    incident = await access.load(db, user, incident_id, edit=True)
    finding = await _finding(db, incident, finding_id)
    views.check_version(finding.version, body.version, "finding")
    if decision is FindingStatus.CONFIRMED and not finding.verifications:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Attach a verification first: the search (or Rowanholt row) that confirms this "
            "on the host or source.",
        )
    if decision is FindingStatus.REFUTED and not body.note.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Say why it's refuted.")
    finding.status = decision
    finding.decided_by = user.id if decision is not FindingStatus.LEAD else None
    finding.decided_at = utcnow() if decision is not FindingStatus.LEAD else None
    finding.decision_note = body.note.strip()
    finding.version += 1
    verb = {"confirmed": "confirmed", "refuted": "refuted", "lead": "reopened as a lead"}[decision.value]
    await activity.record(
        db,
        incident,
        user,
        f"finding_{decision.value}",
        f"{user.name} {verb} FND-{finding.seq}: {finding.title}",
        entity_type="finding",
        entity_id=finding.id,
        note=finding.decision_note,
    )
    await activity.finish(db, user)
    return await views.finding_out(db, user, finding)


@router.post("/{finding_id}/confirm", response_model=FindingOut)
async def confirm(
    incident_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: DecisionIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> FindingOut:
    return await _decide(db, user, incident_id, finding_id, body, FindingStatus.CONFIRMED)


@router.post("/{finding_id}/refute", response_model=FindingOut)
async def refute(
    incident_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: DecisionIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> FindingOut:
    return await _decide(db, user, incident_id, finding_id, body, FindingStatus.REFUTED)


@router.post("/{finding_id}/reopen", response_model=FindingOut)
async def reopen(
    incident_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: DecisionIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> FindingOut:
    return await _decide(db, user, incident_id, finding_id, body, FindingStatus.LEAD)
