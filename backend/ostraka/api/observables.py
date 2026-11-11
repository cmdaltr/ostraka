"""Observables in an incident: add one, pull many from pasted text, record sightings, set verdicts."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incident_schemas import (
    Extracted,
    ExtractIn,
    IncidentObservableOut,
    ObservableIn,
    ObservablePatch,
    SightingIn,
)
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import hides_iocs
from ostraka.models import Asset, Incident, IncidentObservable, Observable, Sighting, User
from ostraka.observables.detect import ObservableType, detect, detect_many, refang

router = APIRouter(prefix="/api/incidents/{incident_id}/observables", tags=["observables"])

# Kinds where case carries meaning (Linux paths, command lines, subjects); the rest compare lower-cased
_CASE_SENSITIVE = frozenset(
    {
        ObservableType.FILE_PATH,
        ObservableType.PROCESS_COMMAND,
        ObservableType.EMAIL_SUBJECT,
        ObservableType.USER_AGENT,
        ObservableType.MUTEX,
        ObservableType.BITCOIN_ADDRESS,
        ObservableType.OTHER,
    }
)


def value_key(kind: ObservableType, value: str) -> str:
    if kind is ObservableType.FILE_PATH and "\\" in value:
        return value.lower()  # Windows paths aren't case-sensitive
    return value if kind in _CASE_SENSITIVE else value.lower()


async def _observable(db: AsyncSession, kind: ObservableType, value: str) -> Observable:
    """The one shared record for this indicator, created on first sight."""
    key = value_key(kind, value)
    await db.execute(
        insert(Observable)
        .values(id=uuid.uuid4(), kind=kind, value=value, value_key=key)
        .on_conflict_do_nothing(constraint="uq_observables_kind_value")
    )
    return await db.scalar(select(Observable).where(Observable.kind == kind, Observable.value_key == key))


async def _check_asset(db: AsyncSession, incident: Incident, asset_id: uuid.UUID | None) -> None:
    if asset_id is not None:
        asset = await db.get(Asset, asset_id)
        if asset is None or asset.incident_id != incident.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That asset isn't in this incident.")


def _label(user: User, observable: Observable) -> str:
    kind = observable.kind.value.replace("_", " ")
    return f"OBS-{observable.seq} ({kind})" if hides_iocs(user) else f"{observable.value} ({kind})"


async def _out(db: AsyncSession, user: User, incident: Incident, items: list[IncidentObservable]):
    seen = await views.elsewhere(db, user, incident.id, [i.observable_id for i in items])
    return [views.observable_out(user, i, seen[i.observable_id]) for i in items]


@router.get("", response_model=list[IncidentObservableOut])
async def list_observables(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
):
    incident = await access.load(db, user, incident_id)
    items = (
        await db.scalars(
            select(IncidentObservable)
            .where(IncidentObservable.incident_id == incident.id)
            .order_by(IncidentObservable.added_at.desc())
        )
    ).all()
    return await _out(db, user, incident, list(items))


@router.post("/extract", response_model=list[Extracted])
async def extract(
    incident_id: uuid.UUID,
    body: ExtractIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[Extracted]:
    """Find every confident indicator in pasted text, without saving anything. The analyst picks
    which to add."""
    incident = await access.load(db, user, incident_id, edit=True)
    found = detect_many(body.text)
    existing = {
        (o.kind, o.value_key)
        for o in (
            await db.scalars(
                select(Observable)
                .join(IncidentObservable, IncidentObservable.observable_id == Observable.id)
                .where(IncidentObservable.incident_id == incident.id)
            )
        ).all()
    }
    return [
        Extracted(
            kind=d.kind,
            value=d.value,
            confidence=d.confidence,
            already_in_incident=(d.kind, value_key(d.kind, d.value)) in existing,
        )
        for d in found
    ]


@router.post("", response_model=IncidentObservableOut, status_code=status.HTTP_201_CREATED)
async def add_observable(
    incident_id: uuid.UUID,
    body: ObservableIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentObservableOut:
    """Add an indicator to the incident (or a sighting, if it's already there). Its type is detected
    when not given."""
    incident = await access.load(db, user, incident_id, edit=True)
    value = refang(body.value)
    kind = body.kind or detect(value).kind
    observable = await _observable(db, kind, value)
    item = await db.scalar(
        select(IncidentObservable).where(
            IncidentObservable.incident_id == incident.id, IncidentObservable.observable_id == observable.id
        )
    )
    created = item is None
    if created:
        item = IncidentObservable(
            incident_id=incident.id,
            observable_id=observable.id,
            verdict=body.verdict,
            note=body.note,
            tags=body.tags,
            added_by=user.id,
        )
        db.add(item)
        await db.flush()
        await activity.record(
            db,
            incident,
            user,
            "observable_added",
            f"{user.name} added {_label(user, observable)}",
            entity_type="observable",
            entity_id=item.id,
            kind=kind.value,
        )
    if body.sighting is not None:
        await _check_asset(db, incident, body.sighting.asset_id)
        db.add(Sighting(incident_observable_id=item.id, recorded_by=user.id, **body.sighting.model_dump()))
        if not created:
            await activity.record(
                db,
                incident,
                user,
                "sighting_added",
                f"{user.name} recorded a sighting of {_label(user, observable)}",
                entity_type="observable",
                entity_id=item.id,
            )
    await activity.finish(db, user)
    await db.refresh(item, ["sightings"])
    return (await _out(db, user, incident, [item]))[0]


async def _item(db: AsyncSession, incident: Incident, item_id: uuid.UUID) -> IncidentObservable:
    item = await db.get(IncidentObservable, item_id)
    if item is None or item.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That observable isn't in this incident.")
    return item


@router.patch("/{item_id}", response_model=IncidentObservableOut)
async def update_observable(
    incident_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ObservablePatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentObservableOut:
    incident = await access.load(db, user, incident_id, edit=True)
    item = await _item(db, incident, item_id)
    views.check_version(item.version, body.version, "observable")
    changes = {
        k: v for k, v in body.model_dump(exclude_unset=True, exclude={"version"}).items() if v is not None
    }
    if changes:
        before = item.verdict
        for field, value in changes.items():
            setattr(item, field, value)
        item.version += 1
        what = (
            f"verdict {before.value} → {item.verdict.value}"
            if "verdict" in changes and before != item.verdict
            else "updated"
        )
        await activity.record(
            db,
            incident,
            user,
            "observable_updated",
            f"{user.name} {what} on {_label(user, item.observable)}",
            entity_type="observable",
            entity_id=item.id,
        )
        await activity.finish(db, user)
    return (await _out(db, user, incident, [item]))[0]


@router.post(
    "/{item_id}/sightings", response_model=IncidentObservableOut, status_code=status.HTTP_201_CREATED
)
async def add_sighting(
    incident_id: uuid.UUID,
    item_id: uuid.UUID,
    body: SightingIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentObservableOut:
    incident = await access.load(db, user, incident_id, edit=True)
    item = await _item(db, incident, item_id)
    await _check_asset(db, incident, body.asset_id)
    db.add(Sighting(incident_observable_id=item.id, recorded_by=user.id, **body.model_dump()))
    await activity.record(
        db,
        incident,
        user,
        "sighting_added",
        f"{user.name} recorded a sighting of {_label(user, item.observable)}",
        entity_type="observable",
        entity_id=item.id,
    )
    await activity.finish(db, user)
    await db.refresh(item, ["sightings"])
    return (await _out(db, user, incident, [item]))[0]


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def remove_observable(
    incident_id: uuid.UUID,
    item_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Take an observable out of this incident. The shared record stays, as do other incidents' links."""
    incident = await access.load(db, user, incident_id, edit=True)
    item = await _item(db, incident, item_id)
    label = _label(user, item.observable)
    await db.delete(item)
    await activity.record(
        db,
        incident,
        user,
        "observable_removed",
        f"{user.name} removed {label}",
        entity_type="observable",
        entity_id=item_id,
    )
    await activity.finish(db, user)
