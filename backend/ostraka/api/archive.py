"""A record's archiving: when it's archived and put in cold store, asking for it back, and asking for it
to be archived ahead of its days.

The rules are `ostraka.archive`. Setting the days is for whoever manages the record, while it's active.
A restore is asked for with a reason by anyone who works records and can see it; archiving early by
whoever manages it. Either is approved or declined by another senior person (an admin, or a lead of a
team on the record) within 24 hours. The decision is one conditional UPDATE, so two approvers can't both
act on it, and a lapsed request can't be approved. Every step goes in the record's activity log and the
security log.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import archive, audit, notify
from ostraka.auth import policy, sessions
from ostraka.auth.deps import current_user
from ostraka.catalogue_schemas import RecordRef, WaitingRequest
from ostraka.db import get_session
from ostraka.incident_schemas import ArchiveNowIn, ArchivingIn, IncidentOut, RestoreDecision, RestoreIn
from ostraka.incidents import access, activity, views
from ostraka.models import (
    ArchiveRequest,
    ArchiveState,
    Incident,
    IncidentStatus,
    NotificationKind,
    RequestAction,
    RequestOutcome,
    User,
    utcnow,
)

router = APIRouter(prefix="/api/incidents/{incident_id}", tags=["archive"])
waiting_router = APIRouter(prefix="/api/archive-requests", tags=["archive"])


def _refuse(reason: str | None, code: int = status.HTTP_403_FORBIDDEN) -> None:
    if reason:
        raise HTTPException(code, reason)


def record_ref(incident: Incident) -> RecordRef:
    return RecordRef(
        id=incident.id,
        ref=incident.ref,
        kind=incident.kind,
        codename=incident.codename,
        title=incident.title,
        archive_state=incident.archive_state,
        closed_at=incident.closed_at,
    )


@router.put("/archiving", response_model=IncidentOut)
async def set_archiving(
    incident_id: uuid.UUID,
    body: ArchivingIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    """When this record is archived and put in cold store, counted in days from closing, or not at all."""
    incident = await access.load(db, user, incident_id, edit=True)
    _refuse(policy.can_set_retention(user, can_manage=access.can_manage(user, incident)))
    views.check_version(incident.version, body.version, "incident")
    before = (incident.auto_archive, incident.archive_after_days, incident.cold_after_days)
    after = (body.auto, body.archive_after_days, body.cold_after_days)
    if before != after:
        incident.auto_archive, incident.archive_after_days, incident.cold_after_days = after
        incident.version += 1
        incident.updated_at = utcnow()
        words = (
            f"archived {body.archive_after_days} days and put in cold store {body.cold_after_days} days "
            "after closing"
            if body.auto
            else "kept out of the archive"
        )
        await activity.record(
            db,
            incident,
            user,
            "archiving_set",
            f"{user.name} set {incident.ref} to be {words}",
            auto=body.auto,
            archive_after_days=body.archive_after_days,
            cold_after_days=body.cold_after_days,
        )
        await activity.finish(db, user)
    return await views.detail(db, user, incident)


async def _ask(
    db: AsyncSession,
    user: User,
    incident: Incident,
    http: Request,
    action: RequestAction,
    to: ArchiveState,
    reason: str,
) -> IncidentOut:
    """Record a request, tell whoever may decide it, and log it."""
    if await archive.pending(db, incident.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A request about {incident.ref} is already waiting.")
    now = utcnow()
    asked = ArchiveRequest(
        id=uuid.uuid4(),
        incident_id=incident.id,
        action=action,
        reason=reason.strip(),
        from_state=incident.archive_state,
        to_state=to,
        requested_by=user.id,
        requested_at=now,
        expires_at=now + archive.REQUEST_WINDOW,
    )
    db.add(asked)
    try:
        await db.flush()
    except IntegrityError as exc:  # someone asked in the same moment
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A request about {incident.ref} is already waiting."
        ) from exc
    what = archive.asked_for(asked, incident.ref)
    await activity.record(
        db,
        incident,
        user,
        "archive_request_made",
        f"{user.name} asked to {what}: {asked.reason}",
        entity_type="archive_request",
        entity_id=asked.id,
        request_kind=action.value,
        to_state=to.value,
    )
    audit.record(
        db,
        "archive_requested",
        actor_id=user.id,
        ip=sessions.client_ip(http),
        incident=incident.ref,
        request=str(asked.id),
        request_kind=action.value,
        to_state=to.value,
    )
    notify.send(
        db,
        await archive.approvers(db, incident, user.id),
        NotificationKind.ARCHIVE_ASKED,
        f"{user.name} asks to {what}",
        body="It needs your approval, or another senior person's. The request lapses in 24 hours.",
        link=notify.incident_link(incident),
        incident=incident,
        actor=user,
    )
    await activity.finish(db, user)
    return await views.detail(db, user, incident)


@router.post("/restore", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
async def ask_restore(
    incident_id: uuid.UUID,
    body: RestoreIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    """Ask for an archived or cold record back."""
    incident = await access.load(db, user, incident_id, sealed_ok=True)
    _refuse(policy.can_ask_restore(user, can_view=True))
    if incident.archive_state is ArchiveState.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{incident.ref} isn't archived.")
    return await _ask(db, user, incident, request, RequestAction.RESTORE, ArchiveState.ACTIVE, body.reason)


@router.post("/archive", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
async def ask_archive(
    incident_id: uuid.UUID,
    body: ArchiveNowIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    """Ask for a closed record to be archived, or put in cold store, now rather than when its days say."""
    incident = await access.load(db, user, incident_id, sealed_ok=True)
    _refuse(policy.can_ask_archive(user, manages=access.manages_record(user, incident)))
    allowed = archive.targets(incident.status, incident.archive_state)
    if body.to_state not in allowed:
        if incident.status is not IncidentStatus.CLOSED:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Close {incident.ref} before archiving it.")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{incident.ref} is {access.STATE_WORDS.get(incident.archive_state, 'active')} already.",
        )
    return await _ask(db, user, incident, request, RequestAction.ARCHIVE, body.to_state, body.reason)


async def _decide(
    db: AsyncSession,
    user: User,
    incident_id: uuid.UUID,
    request_id: uuid.UUID,
    outcome: RequestOutcome,
    note: str,
) -> tuple[Incident, ArchiveRequest, datetime]:
    incident = await access.load(db, user, incident_id, sealed_ok=True)
    asked = await db.get(ArchiveRequest, request_id)
    if asked is None or asked.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That request isn't for this record.")
    if outcome is RequestOutcome.WITHDRAWN:
        _refuse(None if asked.requested_by == user.id else "Only the person who asked can withdraw it.")
    else:
        _refuse(archive.decider_refusal(user, incident, asked))
    now = utcnow()
    if outcome is RequestOutcome.APPROVED and asked.outcome is RequestOutcome.PENDING:
        if stale := archive.still_applies(incident, asked):
            await archive.withdraw_unneeded(db, incident, now, stale)
            await activity.finish(db, user)
            raise HTTPException(status.HTTP_409_CONFLICT, f"{stale} The request is withdrawn.")
    clause = [ArchiveRequest.id == asked.id, ArchiveRequest.outcome == RequestOutcome.PENDING]
    if outcome is RequestOutcome.APPROVED:
        clause.append(ArchiveRequest.expires_at > now)
    won = await db.scalar(
        update(ArchiveRequest)
        .where(*clause)
        .values(outcome=outcome, decided_by=user.id, decided_at=now, note=note.strip())
        .returning(ArchiveRequest.id)
    )
    if won is None:
        await db.rollback()
        await db.refresh(asked)
        if asked.outcome is RequestOutcome.PENDING:  # still pending, so it's past its time
            raise HTTPException(
                status.HTTP_410_GONE, "That request lapsed: nobody approved it within 24 hours. Ask again."
            )
        raise HTTPException(status.HTTP_409_CONFLICT, f"That request was already {asked.outcome.value}.")
    await db.refresh(asked)
    return incident, asked, now


async def _tell_requester(
    db: AsyncSession, incident: Incident, asked: ArchiveRequest, user: User, title: str
) -> None:
    requester = await db.get(User, asked.requested_by) if asked.requested_by else None
    notify.send(
        db,
        [requester] if requester else [],
        NotificationKind.ARCHIVE_DECIDED,
        title,
        body=asked.note,
        link=notify.incident_link(incident),
        incident=incident,
        actor=user,
    )


@router.post("/requests/{request_id}/approve", response_model=IncidentOut)
async def approve(
    incident_id: uuid.UUID,
    request_id: uuid.UUID,
    body: RestoreDecision,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    """Do what was asked: restore the record (closed and active, its days counted again from now), or
    archive it or put it in cold store now."""
    incident, asked, now = await _decide(
        db, user, incident_id, request_id, RequestOutcome.APPROVED, body.note
    )
    lookup = await views.names(db, [asked.requested_by])
    asker = lookup.get(asked.requested_by, "a former member") if asked.requested_by else "a former member"
    if asked.action is RequestAction.RESTORE:
        was = access.STATE_WORDS.get(incident.archive_state, "archived")
        incident.archive_state = ArchiveState.ACTIVE
        incident.archived_at = incident.cold_at = None
        incident.restored_at = now
        incident.version += 1
        incident.updated_at = now
        await activity.record(
            db,
            incident,
            user,
            "record_restored",
            f"{user.name} approved {asker}'s request and restored {incident.ref}, which was {was}",
            entity_type="archive_request",
            entity_id=asked.id,
        )
        title = f"{user.name} restored {incident.ref}"
    else:
        done = "put it in cold store" if asked.to_state is ArchiveState.COLD else "archived it"
        await archive.move(
            db,
            incident,
            asked.to_state,
            now,
            actor=user,
            summary=f"{user.name} approved {asker}'s request and {done} ahead of its days: {asked.reason}",
        )
        title = f"{user.name} approved: {incident.ref} is {access.STATE_WORDS[asked.to_state]}"
    audit.record(
        db,
        "archive_request_approved",
        actor_id=user.id,
        subject_id=asked.requested_by,
        ip=sessions.client_ip(request),
        incident=incident.ref,
        request=str(asked.id),
        request_kind=asked.action.value,
        to_state=asked.to_state.value,
    )
    await _tell_requester(db, incident, asked, user, title)
    await activity.finish(db, user)
    return await views.detail(db, user, incident)


@router.post("/requests/{request_id}/decline", response_model=IncidentOut)
async def decline(
    incident_id: uuid.UUID,
    request_id: uuid.UUID,
    body: RestoreDecision,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    incident, asked, _ = await _decide(db, user, incident_id, request_id, RequestOutcome.DECLINED, body.note)
    what = archive.asked_for(asked, incident.ref)
    reason = f": {asked.note}" if asked.note else ""
    await activity.record(
        db,
        incident,
        user,
        "archive_request_declined",
        f"{user.name} declined the request to {what}{reason}",
        entity_type="archive_request",
        entity_id=asked.id,
    )
    audit.record(
        db,
        "archive_request_declined",
        actor_id=user.id,
        subject_id=asked.requested_by,
        ip=sessions.client_ip(request),
        incident=incident.ref,
        request=str(asked.id),
        request_kind=asked.action.value,
    )
    await _tell_requester(db, incident, asked, user, f"{user.name} declined the request to {what}")
    await activity.finish(db, user)
    return await views.detail(db, user, incident)


@router.delete("/requests/{request_id}", response_model=IncidentOut)
async def withdraw(
    incident_id: uuid.UUID,
    request_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    incident, asked, _ = await _decide(db, user, incident_id, request_id, RequestOutcome.WITHDRAWN, "")
    await activity.record(
        db,
        incident,
        user,
        "archive_request_withdrawn",
        f"{user.name} withdrew the request to {archive.asked_for(asked, incident.ref)}",
        entity_type="archive_request",
        entity_id=asked.id,
    )
    await activity.finish(db, user)
    return await views.detail(db, user, incident)


@waiting_router.get("", response_model=list[WaitingRequest])
async def waiting(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    """Requests this person may decide, oldest first, and their own still waiting."""
    rows = (
        await db.execute(
            select(ArchiveRequest, Incident)
            .join(Incident, Incident.id == ArchiveRequest.incident_id)
            .where(
                ArchiveRequest.outcome == RequestOutcome.PENDING,
                ArchiveRequest.expires_at > utcnow(),
                access.visible_filter(user),
            )
            .order_by(ArchiveRequest.requested_at)
        )
    ).tuples()
    out = []
    pairs = list(rows)
    lookup = await views.names(db, [r.requested_by for r, _ in pairs])
    for asked, incident in pairs:
        shown = archive.request_out(user, incident, asked, lookup)
        if shown.can_decide or shown.can_withdraw:
            out.append(WaitingRequest(record=record_ref(incident), request=shown))
    return out
