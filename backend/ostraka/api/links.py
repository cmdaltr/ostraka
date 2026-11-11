"""Links between records (parent and child, related, duplicate) and promoting a hunt to an incident.

Changing a record's links is a management action on that record; the other end only has to be one
you can see. A record you can't open is counted in `hidden`, never named.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.api.incidents import find_by_ref, open_record
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incident_schemas import IncidentIn, IncidentOut, LinkedRecord, LinkIn, LinksOut, PromoteIn
from ostraka.incidents import access, activity, views
from ostraka.incidents.refs import parse_ref
from ostraka.models import (
    Incident,
    IncidentLink,
    IncidentStatus,
    LinkType,
    RecordKind,
    User,
    utcnow,
)

router = APIRouter(prefix="/api/incidents/{incident_id}", tags=["links"])


def _linked(record: Incident, link: IncidentLink) -> LinkedRecord:
    return LinkedRecord(
        id=record.id,
        link_id=link.id,
        kind=record.kind,
        ref=record.ref,
        title=record.title,
        status=record.status,
        phase=record.phase,
        severity=record.severity,
    )


async def _links_of(db: AsyncSession, incident_id: uuid.UUID) -> list[IncidentLink]:
    return list(
        (
            await db.scalars(
                select(IncidentLink)
                .where(or_(IncidentLink.source_id == incident_id, IncidentLink.target_id == incident_id))
                .order_by(IncidentLink.created_at)
            )
        ).all()
    )


@router.get("/links", response_model=LinksOut)
async def get_links(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> LinksOut:
    incident = await access.load(db, user, incident_id)
    out = LinksOut()
    for link in await _links_of(db, incident.id):
        outgoing = link.source_id == incident.id
        other = await db.get(Incident, link.target_id if outgoing else link.source_id)
        if other is None or not access.can_view(user, other):
            out.hidden += 1
            continue
        item = _linked(other, link)
        match link.kind, outgoing:
            case LinkType.PARENT, True:
                out.children.append(item)
            case LinkType.PARENT, False:
                out.parent = item
            case LinkType.RELATED, _:
                out.related.append(item)
            case LinkType.DUPLICATE, True:
                out.duplicate_of = item
            case LinkType.DUPLICATE, False:
                out.duplicates.append(item)
            case LinkType.PROMOTED, True:
                out.promoted_to.append(item)
            case LinkType.PROMOTED, False:
                out.promoted_from = item
    return out


async def _ancestors(db: AsyncSession, record_id: uuid.UUID) -> set[uuid.UUID]:
    seen: set[uuid.UUID] = set()
    current = record_id
    while True:
        parent = await db.scalar(
            select(IncidentLink.source_id).where(
                IncidentLink.target_id == current, IncidentLink.kind == LinkType.PARENT
            )
        )
        if parent is None or parent in seen:
            return seen
        seen.add(parent)
        current = parent


async def _record_link(db: AsyncSession, user: User, a: Incident, b: Incident, words: str) -> None:
    """The same event on both records' activity chains."""
    for here, there in ((a, b), (b, a)):
        await activity.record(
            db,
            here,
            user,
            "linked",
            f"{user.name} {words.format(here=here.ref, there=there.ref)}",
            entity_type="link",
            other=str(there.id),
        )


@router.post("/links", response_model=LinksOut, status_code=status.HTTP_201_CREATED)
async def add_link(
    incident_id: uuid.UUID,
    body: LinkIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> LinksOut:
    """Link this record to another: it's a child of, the parent of, related to, or a duplicate of it."""
    incident = await access.load(db, user, incident_id, edit=True)
    access.require_manage(user, incident)
    parsed = parse_ref(body.ref)
    other = await find_by_ref(db, parsed) if parsed and parsed.kind == "incident" else None
    if other is None or not access.can_view(user, other):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No incident or hunt {body.ref.lstrip('#')} that you can see."
        )
    if other.id == incident.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A record can't link to itself.")

    pair = f"{incident.ref} and {other.ref}"
    # One link per pair of records, whichever way round it was made
    if await db.scalar(
        select(IncidentLink.id).where(
            or_(
                and_(IncidentLink.source_id == incident.id, IncidentLink.target_id == other.id),
                and_(IncidentLink.source_id == other.id, IncidentLink.target_id == incident.id),
            )
        )
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{pair} are already linked.")

    if body.relation in ("child_of", "parent_of"):
        parent, child = (other, incident) if body.relation == "child_of" else (incident, other)
        if child.id in await _ancestors(db, parent.id) | {parent.id}:
            raise HTTPException(status.HTTP_409_CONFLICT, f"{child.ref} is already above {parent.ref}.")
        link = IncidentLink(source_id=parent.id, target_id=child.id, kind=LinkType.PARENT)
        words = f"made {parent.ref} the parent of {child.ref}"
    elif body.relation == "related_to":
        link = IncidentLink(source_id=incident.id, target_id=other.id, kind=LinkType.RELATED)
        words = "linked {here} and {there} as related"
    else:  # duplicate_of: this record is closed in favour of the other
        if other.status is IncidentStatus.CLOSED:
            raise HTTPException(status.HTTP_409_CONFLICT, f"{other.ref} is closed; reopen it first.")
        link = IncidentLink(source_id=incident.id, target_id=other.id, kind=LinkType.DUPLICATE)
        incident.status, incident.closed_at = IncidentStatus.CLOSED, utcnow()
        incident.version += 1
        words = f"marked {incident.ref} a duplicate of {other.ref} and closed it"
    link.created_by = user.id
    # Worded now: after a rollback the records can't be read without another query
    child_ref = incident.ref if link.target_id == incident.id else other.ref
    db.add(link)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        one_parent = "one_parent" in str(exc.orig)
        problem = f"{child_ref} already has a parent." if one_parent else f"{pair} are already linked."
        raise HTTPException(status.HTTP_409_CONFLICT, problem) from exc
    await _record_link(db, user, incident, other, words)
    await activity.finish(db, user)
    return await get_links(incident.id, user, db)


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def remove_link(
    incident_id: uuid.UUID,
    link_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    incident = await access.load(db, user, incident_id, edit=True)
    access.require_manage(user, incident)
    link = await db.get(IncidentLink, link_id)
    if link is None or incident.id not in (link.source_id, link.target_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such link on this record.")
    other = await db.get(Incident, link.target_id if link.source_id == incident.id else link.source_id)
    await db.delete(link)
    await _record_link(db, user, incident, other, "unlinked {here} from {there}")
    await activity.finish(db, user)


async def promote_hunt(db: AsyncSession, user: User, hunt: Incident, body: PromoteIn) -> Incident:
    """Declare an incident from a hunt, linked to it. Written with the caller's commit."""
    if hunt.kind is not RecordKind.HUNT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Only a hunt can be promoted to an incident."
        )
    declared = IncidentIn(
        kind=RecordKind.INCIDENT,
        title=body.title,
        summary=body.summary or f"Found by {hunt.ref}: {hunt.title}.",
        severity=body.severity,
        tlp=body.tlp or hunt.tlp,
        tags=hunt.tags,
    )
    incident = await open_record(db, user, declared, {t.team_id for t in hunt.teams})
    db.add(IncidentLink(source_id=hunt.id, target_id=incident.id, kind=LinkType.PROMOTED, created_by=user.id))
    await _record_link(db, user, hunt, incident, f"promoted {hunt.ref} to {incident.ref}")
    return incident


@router.post("/promote", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
async def promote(
    incident_id: uuid.UUID,
    body: PromoteIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    """A hunt found something: declare an incident from it. The incident takes the hunt's teams and
    tags, and the two are linked; the hunt stays open until its analysts conclude it."""
    hunt = await access.load(db, user, incident_id, edit=True)
    incident = await promote_hunt(db, user, hunt, body)
    await activity.finish(db, user)
    await db.refresh(incident, ["teams", "members"])
    return await views.detail(db, user, incident)
