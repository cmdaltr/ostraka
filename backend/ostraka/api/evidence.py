"""Evidence items (exhibits, EXH-n) and their chain of custody, and findings citing them.

Custody moves only when the receiver accepts: the holder offers, the person offered accepts with
their own acknowledgement (and, if they re-hashed it, the SHA-256 they got), declines, or the offer
is withdrawn. Each step is a new custody entry, hash-chained to the one before and written to the
incident's activity chain and the security log in the same transaction. Nothing is edited or
deleted; `custody.verify` recomputes the chain on every read.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit, custody, notify
from ostraka.auth import policy
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.evidence_schemas import (
    AcceptIn,
    AnswerIn,
    ChainOut,
    CiteIn,
    CustodyEntryOut,
    EvidenceIn,
    EvidenceOut,
    EvidencePatch,
    OfferIn,
)
from ostraka.incident_schemas import Person
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer, hides_iocs
from ostraka.models import (
    CustodyEntry,
    CustodyKind,
    EvidenceItem,
    Finding,
    FindingEvidence,
    HashCheck,
    Incident,
    NotificationKind,
    User,
    utcnow,
)

router = APIRouter(prefix="/api/incidents/{incident_id}", tags=["evidence"])

ENTITY = "exhibit"  # the activity log's entity type ("evidence" is ACH's)


def _utc(moment: datetime | None) -> datetime:
    moment = moment or utcnow()
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _refuse(problem: str | None, code: int = status.HTTP_403_FORBIDDEN) -> None:
    if problem:
        raise HTTPException(code, problem)


def _person(user_id: uuid.UUID | None, name: str) -> Person | None:
    return Person(id=user_id, name=name) if user_id else None


def _entry_out(user: User, e: CustodyEntry) -> CustodyEntryOut:
    return CustodyEntryOut(
        position=e.position,
        kind=e.kind,
        at=e.at,
        recorded_at=e.recorded_at,
        actor=_person(e.actor_id, e.actor_name),
        from_person=_person(e.from_id, e.from_name),
        to_person=_person(e.to_id, e.to_name),
        purpose=for_viewer(user, e.purpose),
        location=for_viewer(user, e.location),
        acknowledgement=for_viewer(user, e.acknowledgement),
        hash_check=e.hash_check,
        offer_position=e.offer_position,
        hash=e.hash,
    )


async def _citations(db: AsyncSession, evidence_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    out: dict[uuid.UUID, list[dict]] = {i: [] for i in evidence_ids}
    if not evidence_ids:
        return out
    rows = await db.execute(
        select(FindingEvidence.evidence_id, Finding.id, Finding.seq)
        .join(Finding, Finding.id == FindingEvidence.finding_id)
        .where(FindingEvidence.evidence_id.in_(evidence_ids))
        .order_by(Finding.seq)
    )
    for evidence_id, finding_id, seq in rows.all():
        out[evidence_id].append({"id": str(finding_id), "ref": f"FND-{seq}"})
    return out


async def evidence_out(
    db: AsyncSession, user: User, incident: Incident, item: EvidenceItem, cited: list[dict] | None = None
) -> EvidenceOut:
    entries = list(item.entries)
    now = custody.state(item, entries)
    check = custody.verify(item, entries)
    if cited is None:
        cited = (await _citations(db, [item.id]))[item.id]
    masked = hides_iocs(user)
    manage = access.can_manage(user, incident)
    pending = now.pending
    lookup = await views.names(db, [item.created_by])
    return EvidenceOut(
        id=item.id,
        ref=item.ref,
        label=for_viewer(user, item.label),
        description=for_viewer(user, item.description),
        sha256=None if masked else item.sha256,
        sha1=None if masked else item.sha1,
        md5=None if masked else item.md5,
        masked=masked,
        size_bytes=item.size_bytes,
        location=for_viewer(user, now.location),
        acquired_at=item.acquired_at,
        created_by=views.person(lookup, item.created_by),
        created_at=item.created_at,
        holder=_person(now.holder_id, now.holder_name),
        pending=_entry_out(user, pending) if pending else None,
        hash_mismatch=now.mismatch,
        cited_by=cited,
        entries=[_entry_out(user, e) for e in entries],
        chain=ChainOut(
            ok=check.ok,
            entries=check.entries,
            broken_at=check.broken_at,
            head=entries[-1].hash if entries else custody.anchor(item),
        ),
        can_offer=access.can_edit(user, incident)
        and policy.can_offer_custody(
            user, holder_id=now.holder_id, can_manage=manage, pending=pending is not None
        )
        is None,
        can_answer=pending is not None
        and policy.can_answer_custody(user, recipient_id=pending.to_id) is None,
        can_withdraw=pending is not None
        and policy.can_withdraw_custody(
            user, offered_by=pending.actor_id, holder_id=now.holder_id, can_manage=manage
        )
        is None,
        version=item.version,
    )


async def _item(db: AsyncSession, incident: Incident, evidence_id: uuid.UUID, *, lock: bool = False):
    query = select(EvidenceItem).where(EvidenceItem.id == evidence_id)
    if lock:
        # One custody step at a time per exhibit, so positions and hashes follow each other
        query = query.with_for_update().execution_options(populate_existing=True)
    item = await db.scalar(query)
    if item is None or item.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That exhibit isn't in this incident.")
    if lock:
        await db.refresh(item, ["entries"])
    return item


def _append(
    db: AsyncSession,
    item: EvidenceItem,
    actor: User,
    kind: CustodyKind,
    *,
    at: datetime | None = None,
    from_user: tuple[uuid.UUID | None, str] = (None, ""),
    to_user: tuple[uuid.UUID | None, str] = (None, ""),
    purpose: str = "",
    location: str = "",
    acknowledgement: str = "",
    hash_check: HashCheck | None = None,
    offer_position: int | None = None,
) -> CustodyEntry:
    entries = list(item.entries)
    entry = CustodyEntry(
        id=uuid.uuid4(),
        evidence_id=item.id,
        position=len(entries) + 1,
        kind=kind,
        at=_utc(at),
        recorded_at=_utc(None),
        actor_id=actor.id,
        actor_name=actor.name,
        from_id=from_user[0],
        from_name=from_user[1],
        to_id=to_user[0],
        to_name=to_user[1],
        purpose=purpose.strip(),
        location=location.strip(),
        acknowledgement=acknowledgement.strip(),
        hash_check=hash_check,
        offer_position=offer_position,
        prev_hash=entries[-1].hash if entries else custody.anchor(item),
    )
    entry.hash = custody.digest(entry)
    db.add(entry)
    item.entries.append(entry)
    return entry


async def _log(
    db: AsyncSession,
    incident: Incident,
    user: User,
    item: EvidenceItem,
    entry: CustodyEntry,
    action: str,
    summary: str,
    *,
    subject_id: uuid.UUID | None = None,
) -> None:
    """The step on the incident's activity chain, which carries the entry's hash, and the security log."""
    await activity.record(
        db,
        incident,
        user,
        action,
        summary,
        entity_type=ENTITY,
        entity_id=item.id,
        exhibit=item.ref,
        custody_position=entry.position,
        custody_hash=entry.hash,
    )
    audit.record(
        db,
        action,
        actor_id=user.id,
        subject_id=subject_id,
        incident=incident.ref,
        exhibit=item.ref,
        position=entry.position,
        hash=entry.hash,
    )


# ---- exhibits ---------------------------------------------------------------------------------------


@router.get("/evidence", response_model=list[EvidenceOut])
async def list_evidence(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
):
    incident = await access.load(db, user, incident_id)
    items = (
        await db.scalars(
            select(EvidenceItem).where(EvidenceItem.incident_id == incident.id).order_by(EvidenceItem.seq)
        )
    ).all()
    cited = await _citations(db, [i.id for i in items])
    return [await evidence_out(db, user, incident, i, cited[i.id]) for i in items]


@router.get("/evidence/{evidence_id}", response_model=EvidenceOut)
async def get_evidence(
    incident_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> EvidenceOut:
    incident = await access.load(db, user, incident_id)
    return await evidence_out(db, user, incident, await _item(db, incident, evidence_id))


@router.post("/evidence", response_model=EvidenceOut, status_code=status.HTTP_201_CREATED)
async def add_evidence(
    incident_id: uuid.UUID,
    body: EvidenceIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> EvidenceOut:
    """Record an exhibit. Its first custody entry says who holds it: you, unless you name someone."""
    incident = await access.load(db, user, incident_id)
    _refuse(policy.can_add_evidence(user, can_edit=access.can_edit(user, incident)))
    holder = user
    if body.holder_id and body.holder_id != user.id:
        holder = await db.get(User, body.holder_id)
        if holder is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person.")
        _refuse(
            policy.can_receive_custody(holder, can_view=access.can_view(holder, incident), holder_id=None),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    item = EvidenceItem(
        incident_id=incident.id,
        created_by=user.id,
        created_at=_utc(None),
        entries=[],
        **body.model_dump(exclude={"holder_id"}),
    )
    db.add(item)
    await db.flush()
    entry = _append(
        db,
        item,
        user,
        CustodyKind.RECEIVED,
        at=body.acquired_at,
        to_user=(holder.id, holder.name),
        purpose="Recorded as evidence",
        location=body.location,
    )
    held = "" if holder.id == user.id else f", held by {holder.name}"
    await _log(
        db,
        incident,
        user,
        item,
        entry,
        "evidence_added",
        f"{user.name} recorded exhibit {item.ref}: {item.label}{held}",
        subject_id=holder.id,
    )
    await activity.finish(db, user)
    return await evidence_out(db, user, incident, item, [])


@router.patch("/evidence/{evidence_id}", response_model=EvidenceOut)
async def update_evidence(
    incident_id: uuid.UUID,
    evidence_id: uuid.UUID,
    body: EvidencePatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> EvidenceOut:
    incident = await access.load(db, user, incident_id)
    _refuse(policy.can_add_evidence(user, can_edit=access.can_edit(user, incident)))
    item = await _item(db, incident, evidence_id)
    views.check_version(item.version, body.version, "exhibit")
    changes = {
        k: v.strip()
        for k, v in body.model_dump(exclude_unset=True, exclude={"version"}).items()
        if v is not None and getattr(item, k) != v.strip()
    }
    if changes:
        for field, value in changes.items():
            setattr(item, field, value)
        item.version += 1
        await activity.record(
            db,
            incident,
            user,
            "evidence_updated",
            f"{user.name} edited the {' and '.join(sorted(changes))} of {item.ref}",
            entity_type=ENTITY,
            entity_id=item.id,
            fields=sorted(changes),
        )
        await activity.finish(db, user)
    return await evidence_out(db, user, incident, item)


# ---- custody ----------------------------------------------------------------------------------------


@router.post("/evidence/{evidence_id}/offer", response_model=EvidenceOut)
async def offer(
    incident_id: uuid.UUID,
    evidence_id: uuid.UUID,
    body: OfferIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> EvidenceOut:
    """Offer the exhibit to someone. It stays with its holder until they accept."""
    incident = await access.load(db, user, incident_id, edit=True)
    item = await _item(db, incident, evidence_id, lock=True)
    now = custody.state(item, list(item.entries))
    _refuse(
        policy.can_offer_custody(
            user,
            holder_id=now.holder_id,
            can_manage=access.can_manage(user, incident),
            pending=now.pending is not None,
        ),
        status.HTTP_409_CONFLICT if now.pending is not None else status.HTTP_403_FORBIDDEN,
    )
    recipient = await db.get(User, body.to_id)
    if recipient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person.")
    _refuse(
        policy.can_receive_custody(
            recipient, can_view=access.can_view(recipient, incident), holder_id=now.holder_id
        ),
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )
    entry = _append(
        db,
        item,
        user,
        CustodyKind.OFFERED,
        at=body.at,
        from_user=(now.holder_id, now.holder_name),
        to_user=(recipient.id, recipient.name),
        purpose=body.purpose,
        location=body.location,
    )
    on_behalf = "" if user.id == now.holder_id else f" on behalf of {now.holder_name or 'nobody'}"
    await _log(
        db,
        incident,
        user,
        item,
        entry,
        "custody_offered",
        f"{user.name} offered {item.ref} to {recipient.name}{on_behalf}",
        subject_id=recipient.id,
    )
    notify.send(
        db,
        [recipient],
        NotificationKind.NUDGE,
        f"{user.name} is handing you custody of {item.ref}",
        body=body.purpose,
        link=notify.incident_link(incident, "evidence", item.ref),
        incident=incident,
        actor=user,
    )
    await activity.finish(db, user)
    return await evidence_out(db, user, incident, item)


async def _answer(
    db: AsyncSession, user: User, incident_id: uuid.UUID, evidence_id: uuid.UUID
) -> tuple[Incident, EvidenceItem, custody.State, CustodyEntry]:
    incident = await access.load(db, user, incident_id)
    item = await _item(db, incident, evidence_id, lock=True)
    now = custody.state(item, list(item.entries))
    if now.pending is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No handover is waiting on this exhibit.")
    return incident, item, now, now.pending


@router.post("/evidence/{evidence_id}/accept", response_model=EvidenceOut)
async def accept(
    incident_id: uuid.UUID,
    evidence_id: uuid.UUID,
    body: AcceptIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> EvidenceOut:
    """Take custody. The acknowledgement is the receiver's signature: their words, under their own
    sign-in. A SHA-256 that doesn't match is recorded, not refused: the record must show it."""
    incident, item, _, pending = await _answer(db, user, incident_id, evidence_id)
    _refuse(policy.can_answer_custody(user, recipient_id=pending.to_id))
    check = None
    if body.sha256:
        check = HashCheck.MATCH if body.sha256 == item.sha256 else HashCheck.MISMATCH
    entry = _append(
        db,
        item,
        user,
        CustodyKind.ACCEPTED,
        at=body.at,
        from_user=(pending.from_id, pending.from_name),
        to_user=(user.id, user.name),
        purpose=pending.purpose,
        location=pending.location,
        acknowledgement=body.acknowledgement,
        hash_check=check,
        offer_position=pending.position,
    )
    checked = {None: "", HashCheck.MATCH: "; SHA-256 re-checked and matches"}.get(
        check, "; the SHA-256 they computed does NOT match the one recorded"
    )
    await _log(
        db,
        incident,
        user,
        item,
        entry,
        "custody_accepted",
        f"{user.name} took custody of {item.ref} from {pending.from_name or 'nobody'}{checked}",
        subject_id=pending.from_id,
    )
    if pending.actor_id and (offerer := await db.get(User, pending.actor_id)):
        notify.send(
            db,
            [offerer],
            NotificationKind.NUDGE,
            f"{user.name} accepted custody of {item.ref}",
            link=notify.incident_link(incident, "evidence", item.ref),
            incident=incident,
            actor=user,
        )
    await activity.finish(db, user)
    return await evidence_out(db, user, incident, item)


@router.post("/evidence/{evidence_id}/decline", response_model=EvidenceOut)
async def decline(
    incident_id: uuid.UUID,
    evidence_id: uuid.UUID,
    body: AnswerIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> EvidenceOut:
    incident, item, now, pending = await _answer(db, user, incident_id, evidence_id)
    _refuse(policy.can_answer_custody(user, recipient_id=pending.to_id))
    entry = _append(
        db,
        item,
        user,
        CustodyKind.DECLINED,
        from_user=(pending.from_id, pending.from_name),
        to_user=(user.id, user.name),
        acknowledgement=body.note,
        offer_position=pending.position,
    )
    await _log(
        db,
        incident,
        user,
        item,
        entry,
        "custody_declined",
        f"{user.name} declined custody of {item.ref}; it stays with {now.holder_name or 'nobody'}",
        subject_id=pending.actor_id,
    )
    if pending.actor_id and (offerer := await db.get(User, pending.actor_id)):
        notify.send(
            db,
            [offerer],
            NotificationKind.NUDGE,
            f"{user.name} declined custody of {item.ref}",
            body=body.note,
            link=notify.incident_link(incident, "evidence", item.ref),
            incident=incident,
            actor=user,
        )
    await activity.finish(db, user)
    return await evidence_out(db, user, incident, item)


@router.post("/evidence/{evidence_id}/withdraw", response_model=EvidenceOut)
async def withdraw(
    incident_id: uuid.UUID,
    evidence_id: uuid.UUID,
    body: AnswerIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> EvidenceOut:
    incident, item, now, pending = await _answer(db, user, incident_id, evidence_id)
    _refuse(
        policy.can_withdraw_custody(
            user,
            offered_by=pending.actor_id,
            holder_id=now.holder_id,
            can_manage=access.can_manage(user, incident),
        )
    )
    entry = _append(
        db,
        item,
        user,
        CustodyKind.WITHDRAWN,
        from_user=(pending.from_id, pending.from_name),
        to_user=(pending.to_id, pending.to_name),
        acknowledgement=body.note,
        offer_position=pending.position,
    )
    await _log(
        db,
        incident,
        user,
        item,
        entry,
        "custody_withdrawn",
        f"{user.name} withdrew the handover of {item.ref} to {pending.to_name}",
        subject_id=pending.to_id,
    )
    await activity.finish(db, user)
    return await evidence_out(db, user, incident, item)


# ---- findings citing exhibits -----------------------------------------------------------------------


async def _finding(db: AsyncSession, incident: Incident, finding_id: uuid.UUID) -> Finding:
    finding = await db.get(Finding, finding_id)
    if finding is None or finding.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That finding isn't in this incident.")
    return finding


@router.post(
    "/findings/{finding_id}/evidence", response_model=EvidenceOut, status_code=status.HTTP_201_CREATED
)
async def cite(
    incident_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: CiteIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> EvidenceOut:
    """Cite an exhibit from a finding. Citing it twice is harmless."""
    incident = await access.load(db, user, incident_id)
    _refuse(policy.can_add_evidence(user, can_edit=access.can_edit(user, incident)))
    finding = await _finding(db, incident, finding_id)
    item = await _item(db, incident, body.evidence_id)
    if await db.get(FindingEvidence, (finding.id, item.id)) is None:
        db.add(FindingEvidence(finding_id=finding.id, evidence_id=item.id, cited_by=user.id))
        await activity.record(
            db,
            incident,
            user,
            "evidence_cited",
            f"{user.name} cited {item.ref} in FND-{finding.seq}",
            entity_type="finding",
            entity_id=finding.id,
            exhibit=item.ref,
        )
        await activity.finish(db, user)
    return await evidence_out(db, user, incident, item)


@router.delete(
    "/findings/{finding_id}/evidence/{evidence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def uncite(
    incident_id: uuid.UUID,
    finding_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    incident = await access.load(db, user, incident_id)
    _refuse(policy.can_add_evidence(user, can_edit=access.can_edit(user, incident)))
    finding = await _finding(db, incident, finding_id)
    item = await _item(db, incident, evidence_id)
    removed = await db.execute(
        delete(FindingEvidence).where(
            FindingEvidence.finding_id == finding.id, FindingEvidence.evidence_id == item.id
        )
    )
    if not removed.rowcount:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"FND-{finding.seq} doesn't cite {item.ref}.")
    await activity.record(
        db,
        incident,
        user,
        "evidence_uncited",
        f"{user.name} removed {item.ref} from FND-{finding.seq}",
        entity_type="finding",
        entity_id=finding.id,
        exhibit=item.ref,
    )
    await activity.finish(db, user)
