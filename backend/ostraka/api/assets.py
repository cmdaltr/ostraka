"""Assets (systems of interest) in an incident, patient zero, and the links between them."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incident_schemas import AssetIn, AssetLinkIn, AssetLinkOut, AssetOut, AssetPatch
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer, hides_iocs
from ostraka.models import Asset, AssetLink, Incident, User

router = APIRouter(prefix="/api/incidents/{incident_id}", tags=["assets"])


def _out(user: User, asset: Asset) -> AssetOut:
    out = AssetOut(
        id=asset.id,
        ref=f"AST-{asset.seq}",
        created_at=asset.created_at,
        version=asset.version,
        **{f: getattr(asset, f) for f in AssetIn.model_fields},
    )
    if hides_iocs(user):
        out.ip = out.public_ip = ""
    return out


def _link_out(user: User, link: AssetLink) -> AssetLinkOut:
    out = AssetLinkOut.model_validate(link, from_attributes=True)
    out.note = for_viewer(user, out.note)
    return out


async def _clear_patient_zero(db: AsyncSession, incident: Incident, keep: uuid.UUID | None) -> None:
    query = update(Asset).where(Asset.incident_id == incident.id, Asset.is_patient_zero.is_(True))
    if keep is not None:
        query = query.where(Asset.id != keep)
    await db.execute(query.values(is_patient_zero=False))


async def _asset(db: AsyncSession, incident: Incident, asset_id: uuid.UUID) -> Asset:
    asset = await db.get(Asset, asset_id)
    if asset is None or asset.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That asset isn't in this incident.")
    return asset


@router.get("/assets", response_model=list[AssetOut])
async def list_assets(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
):
    await access.load(db, user, incident_id)
    rows = await db.scalars(
        select(Asset)
        .where(Asset.incident_id == incident_id)
        .order_by(Asset.is_patient_zero.desc(), Asset.hostname)
    )
    return [_out(user, a) for a in rows.all()]


@router.post("/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def add_asset(
    incident_id: uuid.UUID,
    body: AssetIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AssetOut:
    incident = await access.load(db, user, incident_id, edit=True)
    if body.is_patient_zero:
        await _clear_patient_zero(db, incident, None)
    asset = Asset(incident_id=incident.id, created_by=user.id, **body.model_dump())
    db.add(asset)
    await db.flush()
    summary = f"{user.name} added asset {asset.hostname}" + (
        " as patient zero" if asset.is_patient_zero else ""
    )
    await activity.record(db, incident, user, "asset_added", summary, entity_type="asset", entity_id=asset.id)
    await activity.finish(db, user)
    return _out(user, asset)


@router.patch("/assets/{asset_id}", response_model=AssetOut)
async def update_asset(
    incident_id: uuid.UUID,
    asset_id: uuid.UUID,
    body: AssetPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AssetOut:
    incident = await access.load(db, user, incident_id, edit=True)
    asset = await _asset(db, incident, asset_id)
    views.check_version(asset.version, body.version, "asset")
    changes = {
        k: v for k, v in body.model_dump(exclude_unset=True, exclude={"version"}).items() if v is not None
    }
    if changes.get("is_patient_zero"):
        await _clear_patient_zero(db, incident, asset.id)
        await db.flush()
    described = []
    for field, value in changes.items():
        if getattr(asset, field) != value:
            before = getattr(asset, field)
            setattr(asset, field, value)
            if field == "is_patient_zero":
                described.append("marked patient zero" if value else "unmarked patient zero")
            elif field in ("status", "criticality"):
                described.append(f"{field} {before.value} → {value.value}")
            else:
                described.append(f"changed {field.replace('_', ' ')}")
    if described:
        asset.version += 1
        await activity.record(
            db,
            incident,
            user,
            "asset_updated",
            f"{user.name} {'; '.join(described)} on {asset.hostname}",
            entity_type="asset",
            entity_id=asset.id,
        )
        await activity.finish(db, user)
    return _out(user, asset)


@router.get("/asset-links", response_model=list[AssetLinkOut])
async def list_links(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
):
    await access.load(db, user, incident_id)
    rows = await db.scalars(
        select(AssetLink).where(AssetLink.incident_id == incident_id).order_by(AssetLink.seen_at)
    )
    return [_link_out(user, link) for link in rows.all()]


@router.post("/asset-links", response_model=AssetLinkOut, status_code=status.HTTP_201_CREATED)
async def add_link(
    incident_id: uuid.UUID,
    body: AssetLinkIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> AssetLinkOut:
    incident = await access.load(db, user, incident_id, edit=True)
    source, target = await _asset(db, incident, body.source_id), await _asset(db, incident, body.target_id)
    link = AssetLink(incident_id=incident.id, created_by=user.id, **body.model_dump())
    db.add(link)
    await db.flush()
    await activity.record(
        db,
        incident,
        user,
        "asset_link_added",
        f"{user.name} recorded {body.kind.value.replace('_', ' ')} "
        f"from {source.hostname} to {target.hostname}",
        entity_type="asset_link",
        entity_id=link.id,
    )
    await activity.finish(db, user)
    return _link_out(user, link)


@router.delete("/asset-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def remove_link(
    incident_id: uuid.UUID,
    link_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    incident = await access.load(db, user, incident_id, edit=True)
    link = await db.get(AssetLink, link_id)
    if link is None or link.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such link in this incident.")
    await db.delete(link)
    await activity.record(
        db,
        incident,
        user,
        "asset_link_removed",
        f"{user.name} removed an asset link",
        entity_type="asset_link",
        entity_id=link_id,
    )
    await activity.finish(db, user)
