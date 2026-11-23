"""Keeping closed records: archived after a while, then cold store, and back only by an approved restore.

A closed record is archived `archive_after_days` (30) after it closed and put in cold store after
`cold_after_days` (90), each counted from closing or from the last restore, whichever is later. Both are
the record's own and can be changed, or archiving switched off, by whoever manages it. Archived means
read only. Cold store means its catalogue entry and its lessons stay readable and the rest waits until
it's restored. A restore is asked for with a reason and approved by another senior person (an admin, or
a lead of a team on the record) within 24 hours, or it lapses. Approved, the record is closed and active
again, and its days start over.

A record can also be archived (or put in cold store) ahead of its days: whoever manages it asks, with
a reason, and it needs the same approval. One request waits per record at a time; reopening the record,
or Ostraka moving it on first, withdraws an archive request that's no longer needed.

`state_due`, `next_move` and `targets` are pure; `tick` is called by the scheduler inside its advisory
lock, and the caller commits.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import notify
from ostraka.auth import policy
from ostraka.incident_schemas import ArchiveOut, ArchiveRequestOut
from ostraka.incidents import access, activity
from ostraka.log import get_logger
from ostraka.models import (
    ArchiveRequest,
    ArchiveState,
    Incident,
    IncidentStatus,
    NotificationKind,
    RequestAction,
    RequestOutcome,
    Role,
    User,
)

log = get_logger(__name__)

REQUEST_WINDOW = timedelta(hours=24)
ORDER = (ArchiveState.ACTIVE, ArchiveState.ARCHIVED, ArchiveState.COLD)
WORDS = {ArchiveState.ACTIVE: "active", ArchiveState.ARCHIVED: "archived", ArchiveState.COLD: "in cold store"}


def counted_from(closed_at: datetime | None, restored_at: datetime | None) -> datetime | None:
    """When a record's days start: its closing, or its last restore if that's later. Pure."""
    if closed_at is None:
        return None
    return max(closed_at, restored_at) if restored_at else closed_at


def state_due(
    *,
    status_: IncidentStatus,
    closed_at: datetime | None,
    restored_at: datetime | None,
    auto: bool,
    archive_days: int,
    cold_days: int,
    now: datetime,
) -> ArchiveState:
    """Where a record should be now. An open record, or one kept out of archiving, stays active. Pure."""
    start = counted_from(closed_at, restored_at)
    if status_ is not IncidentStatus.CLOSED or not auto or start is None:
        return ArchiveState.ACTIVE
    age = now - start
    if age >= timedelta(days=cold_days):
        return ArchiveState.COLD
    if age >= timedelta(days=archive_days):
        return ArchiveState.ARCHIVED
    return ArchiveState.ACTIVE


def next_move(
    *,
    status_: IncidentStatus,
    closed_at: datetime | None,
    restored_at: datetime | None,
    auto: bool,
    archive_days: int,
    cold_days: int,
    state: ArchiveState,
) -> tuple[ArchiveState, datetime] | None:
    """The next state the record will move to by itself, and when. None if it won't. Pure."""
    start = counted_from(closed_at, restored_at)
    if status_ is not IncidentStatus.CLOSED or not auto or start is None or state is ArchiveState.COLD:
        return None
    if state is ArchiveState.ACTIVE:
        return ArchiveState.ARCHIVED, start + timedelta(days=archive_days)
    return ArchiveState.COLD, start + timedelta(days=cold_days)


def targets(status_: IncidentStatus, state: ArchiveState) -> list[ArchiveState]:
    """Where a record may be archived to ahead of its days: further along, and only once closed. Pure."""
    if status_ is not IncidentStatus.CLOSED:
        return []
    return list(ORDER[ORDER.index(state) + 1 :])


def still_applies(incident: Incident, request: ArchiveRequest) -> str | None:
    """Why approving `request` would no longer do anything, or None if it would."""
    if request.action is RequestAction.RESTORE:
        return (
            None if incident.archive_state is not ArchiveState.ACTIVE else f"{incident.ref} isn't archived."
        )
    if incident.status is not IncidentStatus.CLOSED:
        return f"{incident.ref} was reopened, so it isn't archived."
    if request.to_state not in targets(incident.status, incident.archive_state):
        return f"{incident.ref} is {access.STATE_WORDS[incident.archive_state]} already."
    return None


ASK_WORDS = {RequestAction.RESTORE: "restore", RequestAction.ARCHIVE: "archive"}


def asked_for(request: ArchiveRequest, ref: str) -> str:
    """What a request asks for, as words: restore OST-26-014, put OST-26-014 in cold store."""
    if request.action is RequestAction.ARCHIVE and request.to_state is ArchiveState.COLD:
        return f"put {ref} in cold store"
    return f"{ASK_WORDS[request.action]} {ref}"


async def approvers(db: AsyncSession, incident: Incident, requester_id) -> list[User]:
    """Who may decide a request on this record: admins and the leads of its teams, not the requester."""
    team_ids = [t.team_id for t in incident.teams]
    rows = await db.scalars(select(User).where(User.is_active, User.role.in_((Role.ADMIN, Role.TEAM_LEAD))))
    senior = [u for u in rows.all() if u.role is Role.ADMIN or (u.team_id and u.team_id in team_ids)]
    return [u for u in senior if u.id != requester_id]


async def pending(db: AsyncSession, incident_id) -> ArchiveRequest | None:
    return await db.scalar(
        select(ArchiveRequest).where(
            ArchiveRequest.incident_id == incident_id, ArchiveRequest.outcome == RequestOutcome.PENDING
        )
    )


async def withdraw_unneeded(db: AsyncSession, incident: Incident, now: datetime, why: str) -> None:
    """Withdraw a waiting request that no longer applies (the record reopened, or moved on first)."""
    request = await pending(db, incident.id)
    if request is None or still_applies(incident, request) is None:
        return
    request.outcome, request.decided_at, request.note = RequestOutcome.WITHDRAWN, now, why
    await activity.record(
        db,
        incident,
        None,
        "archive_request_withdrawn",
        f"Ostraka withdrew the request to {asked_for(request, incident.ref)}: {why}",
        entity_type="archive_request",
        entity_id=request.id,
    )


async def move(
    db: AsyncSession,
    incident: Incident,
    to: ArchiveState,
    now: datetime,
    *,
    actor: User | None = None,
    summary: str = "",
) -> None:
    """Archive the record or put it in cold store, and say so in its log."""
    before = incident.archive_state
    incident.archive_state = to
    if to is ArchiveState.ARCHIVED or (to is ArchiveState.COLD and incident.archived_at is None):
        incident.archived_at = now
    if to is ArchiveState.COLD:
        incident.cold_at = now
    incident.version += 1
    incident.updated_at = now
    if not summary:
        days = incident.cold_after_days if to is ArchiveState.COLD else incident.archive_after_days
        restored = incident.restored_at is not None and incident.restored_at > (incident.closed_at or now)
        since = "restored" if restored else "closed"
        done = "archived" if to is ArchiveState.ARCHIVED else "put in cold store"
        summary = f"Ostraka {done} {incident.ref}: {since} {days} days ago"
    await activity.record(
        db,
        incident,
        actor,
        "record_archived" if to is ArchiveState.ARCHIVED else "record_cold_stored",
        summary,
        before=before.value,
        after=to.value,
    )


async def tick(db: AsyncSession, now: datetime) -> int:
    """Move closed records on, and let unanswered requests lapse. Returns how many changed."""
    acted = 0
    rows = await db.scalars(
        select(Incident).where(
            Incident.status == IncidentStatus.CLOSED,
            Incident.auto_archive.is_(True),
            Incident.archive_state != ArchiveState.COLD,
        )
    )
    for incident in rows.unique().all():
        due = state_due(
            status_=incident.status,
            closed_at=incident.closed_at,
            restored_at=incident.restored_at,
            auto=incident.auto_archive,
            archive_days=incident.archive_after_days,
            cold_days=incident.cold_after_days,
            now=now,
        )
        if ORDER.index(due) > ORDER.index(incident.archive_state):
            await move(db, incident, due, now)
            await withdraw_unneeded(db, incident, now, "its days came first")
            acted += 1

    lapsed = (
        await db.scalars(
            update(ArchiveRequest)
            .where(ArchiveRequest.outcome == RequestOutcome.PENDING, ArchiveRequest.expires_at <= now)
            .values(outcome=RequestOutcome.EXPIRED, decided_at=now)
            .returning(ArchiveRequest)
        )
    ).all()
    for request in lapsed:
        incident = await db.get(Incident, request.incident_id)
        if incident is None:
            continue
        what = asked_for(request, incident.ref)
        await activity.record(
            db,
            incident,
            None,
            "archive_request_lapsed",
            f"The request to {what} lapsed: nobody approved it within 24 hours",
            entity_type="archive_request",
            entity_id=request.id,
        )
        requester = await db.get(User, request.requested_by) if request.requested_by else None
        notify.send(
            db,
            [requester] if requester else [],
            NotificationKind.ARCHIVE_DECIDED,
            f"Your request to {what} lapsed",
            body="Nobody approved it within 24 hours. Ask again if you still need it.",
            link=notify.incident_link(incident),
            incident=incident,
        )
        acted += 1
    return acted


def leads_a_team_on(user: User, incident: Incident) -> bool:
    return (
        user.role is Role.TEAM_LEAD
        and user.team_id is not None
        and any(t.team_id == user.team_id for t in incident.teams)
    )


def request_out(user: User, incident: Incident, request: ArchiveRequest, lookup: dict) -> ArchiveRequestOut:
    from ostraka.incidents.views import person

    return ArchiveRequestOut(
        id=request.id,
        action=request.action,
        reason=request.reason,
        from_state=request.from_state,
        to_state=request.to_state,
        requested_by=person(lookup, request.requested_by),
        requested_at=request.requested_at,
        expires_at=request.expires_at,
        outcome=request.outcome,
        decided_by=person(lookup, request.decided_by),
        decided_at=request.decided_at,
        note=request.note,
        can_decide=request.outcome is RequestOutcome.PENDING
        and decider_refusal(user, incident, request) is None,
        can_withdraw=request.outcome is RequestOutcome.PENDING and request.requested_by == user.id,
    )


def decider_refusal(user: User, incident: Incident, request: ArchiveRequest) -> str | None:
    """Why `user` may not approve or decline `request`, or None."""
    return policy.can_decide_archive_request(
        user, requester_id=request.requested_by, leads_a_team_on_it=leads_a_team_on(user, incident)
    )


async def view(db: AsyncSession, user: User, incident: Incident) -> ArchiveOut:
    """The record's keeping as this viewer sees it."""
    from ostraka.incidents.views import names

    request = await pending(db, incident.id)
    nxt = next_move(
        status_=incident.status,
        closed_at=incident.closed_at,
        restored_at=incident.restored_at,
        auto=incident.auto_archive,
        archive_days=incident.archive_after_days,
        cold_days=incident.cold_after_days,
        state=incident.archive_state,
    )
    out = None
    if request is not None:
        lookup = await names(db, [request.requested_by, request.decided_by])
        out = request_out(user, incident, request, lookup)
    manages = access.manages_record(user, incident)
    early = targets(incident.status, incident.archive_state)
    return ArchiveOut(
        state=incident.archive_state,
        auto=incident.auto_archive,
        archive_after_days=incident.archive_after_days,
        cold_after_days=incident.cold_after_days,
        archived_at=incident.archived_at,
        cold_at=incident.cold_at,
        restored_at=incident.restored_at,
        next_state=nxt[0] if nxt else None,
        next_at=nxt[1] if nxt else None,
        can_set=incident.archive_state is ArchiveState.ACTIVE
        and policy.can_set_retention(user, can_manage=access.can_manage(user, incident)) is None,
        can_restore=incident.archive_state is not ArchiveState.ACTIVE
        and request is None
        and policy.can_ask_restore(user, can_view=access.can_view(user, incident)) is None,
        archive_to=early if request is None and policy.can_ask_archive(user, manages=manages) is None else [],
        request=out,
    )
