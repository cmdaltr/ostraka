"""Regulator clocks on an incident: add, change, close and reopen them, and mark milestones met.

Adding, changing and closing are for the incident's commander or comms (or an admin); marking a
milestone met is for them and the clock's owner (`policy.can_manage_clocks`, `can_mark_milestone`).
Each of these is on the activity log and in the security log.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit, notify
from ostraka.auth import policy, sessions
from ostraka.auth.deps import current_user
from ostraka.clock_schemas import (
    ClockClose,
    ClockIn,
    ClockIncident,
    ClockOut,
    ClockPatch,
    MilestoneMet,
    MyDeadline,
    RegimeMilestoneOut,
    RegimeOut,
)
from ostraka.clocks import regimes, service
from ostraka.clocks.nudges import label
from ostraka.db import get_session
from ostraka.incident_schemas import Versioned
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer
from ostraka.models import (
    Clock,
    Incident,
    IncidentMember,
    IncidentRole,
    NotificationKind,
    RecordKind,
    RegimeKey,
    Role,
    User,
    utcnow,
)

router = APIRouter(prefix="/api/incidents/{incident_id}/clocks", tags=["clocks"])
mine_router = APIRouter(prefix="/api/clocks", tags=["clocks"])

# A little slack for a browser clock that runs ahead of the server's
FUTURE_SLACK = timedelta(minutes=2)


def _refuse(refusal: str | None) -> None:
    if refusal:
        raise HTTPException(status.HTTP_403_FORBIDDEN, refusal)


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, message)


async def _clock(db: AsyncSession, incident: Incident, clock_id: uuid.UUID) -> Clock:
    clock = await db.get(Clock, clock_id)
    if clock is None or clock.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That clock isn't in this incident.")
    return clock


async def _owner(db: AsyncSession, incident: Incident, owner_id: uuid.UUID) -> User:
    owner = await db.get(User, owner_id)
    if owner is None or not owner.is_active or not access.can_view(owner, incident):
        raise _unprocessable("A clock's owner must be someone who can see this incident.")
    if owner.role is Role.OBSERVER:
        raise _unprocessable("Observers can't own a regulator clock.")
    return owner


def _past(moment: datetime | None, what: str) -> None:
    if moment is not None and moment > utcnow() + FUTURE_SLACK:
        raise _unprocessable(f"{what} can't be in the future.")


def _custom(regime: regimes.Regime, milestones: list, title: str) -> list[dict]:
    definitions = [m.model_dump(mode="json") for m in milestones]
    if not regime.custom:
        if definitions:
            raise _unprocessable(f"{regime.name}'s milestones are fixed; only a custom clock takes its own.")
        return []
    if not title.strip():
        raise _unprocessable("Name a custom clock: whose deadline it is (an insurer, a contract, the board).")
    refusal = regimes.check_custom(definitions)
    if refusal:
        raise _unprocessable(refusal)
    return definitions


def _incident_of_kind(incident: Incident) -> None:
    if incident.kind is not RecordKind.INCIDENT:
        raise _unprocessable("Regulator clocks go on incidents, not hunts.")


@router.get("", response_model=list[ClockOut])
async def list_clocks(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[ClockOut]:
    incident = await access.load(db, user, incident_id)
    rows = await db.scalars(select(Clock).where(Clock.incident_id == incident.id).order_by(Clock.seq))
    now = utcnow()
    return [await service.clock_out(db, user, incident, clock, now) for clock in rows.all()]


@router.post("", response_model=ClockOut, status_code=status.HTTP_201_CREATED)
async def add_clock(
    incident_id: uuid.UUID,
    body: ClockIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ClockOut:
    incident = await access.load(db, user, incident_id, edit=True)
    _incident_of_kind(incident)
    _refuse(policy.can_manage_clocks(user, service.role_in(user, incident)))
    regime = regimes.REGIMES[body.regime.value]
    title = body.title.strip()
    definitions = _custom(regime, body.milestones, title)
    _past(body.starts_at, "The time you became aware")
    _past(body.classified_at, "The classification time")
    owner = await _owner(db, incident, body.owner_id or user.id)
    clock = Clock(
        incident_id=incident.id,
        regime=body.regime,
        title=title,
        owner_id=owner.id,
        starts_at=body.starts_at,
        classified_at=body.classified_at,
        timezone=body.timezone or regime.zone,
        milestones=definitions,
        close_reason="",
        created_by=user.id,
    )
    service.sync_marks(clock)
    db.add(clock)
    await db.flush()
    now = utcnow()
    service.settle_nudges(clock, incident, now)
    first = next((d for d in service.dues(clock, incident) if d.due is not None), None)
    due_words = (
        f", first deadline {first.due:%d %b %H:%M} UTC ({first.name.lower()})" if first and first.due else ""
    )
    await activity.record(
        db,
        incident,
        user,
        "clock_added",
        f"{user.name} started {label(clock)}, owned by {owner.name}{due_words}",
        entity_type="clock",
        entity_id=clock.id,
        regime=body.regime.value,
    )
    audit.record(
        db,
        "clock_added",
        actor_id=user.id,
        subject_id=owner.id,
        ip=sessions.client_ip(request),
        incident=incident.ref,
        clock=clock.ref,
        regime=body.regime.value,
    )
    _tell_owner(db, user, incident, clock, owner, first)
    await activity.finish(db, user)
    return await service.clock_out(db, user, incident, clock, now)


def _tell_owner(
    db: AsyncSession, actor: User, incident: Incident, clock: Clock, owner: User, first: regimes.Due | None
) -> None:
    due = f": first deadline {first.due:%d %b %H:%M} UTC" if first and first.due else ""
    notify.send(
        db,
        [owner],
        NotificationKind.CLOCK_DUE,
        f"You own {label(clock)}{due}",
        body=f"{actor.name} gave you this regulator clock in {incident.ref}, {incident.title}.",
        link=notify.incident_link(incident, "clocks", clock.ref),
        incident=incident,
        actor=actor,
    )


@router.patch("/{clock_id}", response_model=ClockOut)
async def update_clock(
    incident_id: uuid.UUID,
    clock_id: uuid.UUID,
    body: ClockPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ClockOut:
    incident = await access.load(db, user, incident_id, edit=True)
    clock = await _clock(db, incident, clock_id)
    _refuse(policy.can_manage_clocks(user, service.role_in(user, incident)))
    views.check_version(clock.version, body.version, "clock")
    sent = body.model_dump(exclude_unset=True, exclude={"version"})
    changed: list[str] = []
    new_owner = None
    if "title" in sent and sent["title"] is not None and sent["title"].strip() != clock.title:
        clock.title = sent["title"].strip()
        changed.append("title")
    if sent.get("owner_id") and sent["owner_id"] != clock.owner_id:
        new_owner = await _owner(db, incident, sent["owner_id"])
        clock.owner_id = new_owner.id
        changed.append("owner")
    for field, what in (
        ("starts_at", "The time you became aware"),
        ("classified_at", "The classification time"),
    ):
        if field in sent and sent[field] != getattr(clock, field):
            _past(sent[field], what)
            setattr(clock, field, sent[field])  # None puts it back: the incident's aware_at, unclassified
            changed.append(field)
    if sent.get("timezone") and sent["timezone"] != clock.timezone:
        clock.timezone = sent["timezone"]
        changed.append("timezone")
    if body.milestones is not None:
        definitions = _custom(service.regime_of(clock), body.milestones, clock.title)
        met = [m.key for m in clock.marks if m.met_at is not None]
        if any(int(key[1:]) > len(definitions) for key in met):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A milestone already met can't be taken off the clock."
            )
        if definitions != clock.milestones:
            clock.milestones = definitions
            service.sync_marks(clock)
            changed.append("milestones")
    if service.regime_of(clock).custom and not clock.title:
        raise _unprocessable("A custom clock keeps its name.")
    if changed:
        clock.version += 1
        service.settle_nudges(clock, incident, utcnow())
        await activity.record(
            db,
            incident,
            user,
            "clock_updated",
            f"{user.name} changed {label(clock)}: {', '.join(f.replace('_', ' ') for f in changed)}",
            entity_type="clock",
            entity_id=clock.id,
            fields=changed,
        )
        if new_owner is not None:
            first = next(
                (
                    d
                    for d in service.dues(clock, incident)
                    if d.due and not service.marks(clock)[d.key].met_at
                ),
                None,
            )
            _tell_owner(db, user, incident, clock, new_owner, first)
        await activity.finish(db, user)
    return await service.clock_out(db, user, incident, clock, utcnow())


@router.post("/{clock_id}/close", response_model=ClockOut)
async def close_clock(
    incident_id: uuid.UUID,
    clock_id: uuid.UUID,
    body: ClockClose,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ClockOut:
    """Stop a clock: not reportable after all, or every milestone is in. Its nudges stop."""
    incident = await access.load(db, user, incident_id, edit=True)
    clock = await _clock(db, incident, clock_id)
    _refuse(policy.can_manage_clocks(user, service.role_in(user, incident)))
    views.check_version(clock.version, body.version, "clock")
    if clock.closed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That clock is already closed.")
    clock.closed_at, clock.closed_by, clock.close_reason = utcnow(), user.id, body.reason.strip()
    clock.version += 1
    await activity.record(
        db,
        incident,
        user,
        "clock_closed",
        f"{user.name} closed {label(clock)}: {clock.close_reason}",
        entity_type="clock",
        entity_id=clock.id,
    )
    audit.record(
        db,
        "clock_closed",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        incident=incident.ref,
        clock=clock.ref,
    )
    await activity.finish(db, user)
    return await service.clock_out(db, user, incident, clock, utcnow())


@router.post("/{clock_id}/reopen", response_model=ClockOut)
async def reopen_clock(
    incident_id: uuid.UUID,
    clock_id: uuid.UUID,
    body: Versioned,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ClockOut:
    incident = await access.load(db, user, incident_id, edit=True)
    clock = await _clock(db, incident, clock_id)
    _refuse(policy.can_manage_clocks(user, service.role_in(user, incident)))
    views.check_version(clock.version, body.version, "clock")
    if clock.closed_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That clock is running.")
    clock.closed_at, clock.closed_by, clock.close_reason = None, None, ""
    clock.version += 1
    service.settle_nudges(clock, incident, utcnow())
    await activity.record(
        db,
        incident,
        user,
        "clock_reopened",
        f"{user.name} reopened {label(clock)}",
        entity_type="clock",
        entity_id=clock.id,
    )
    audit.record(
        db,
        "clock_reopened",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        incident=incident.ref,
        clock=clock.ref,
    )
    await activity.finish(db, user)
    return await service.clock_out(db, user, incident, clock, utcnow())


async def _marking(
    db: AsyncSession, user: User, incident_id: uuid.UUID, clock_id: uuid.UUID, key: str, version: int
) -> tuple[Incident, Clock, regimes.Due]:
    incident = await access.load(db, user, incident_id, edit=True)
    clock = await _clock(db, incident, clock_id)
    _refuse(
        policy.can_mark_milestone(user, service.role_in(user, incident), is_owner=clock.owner_id == user.id)
    )
    views.check_version(clock.version, version, "clock")
    if clock.closed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That clock is closed; reopen it first.")
    due = next((d for d in service.dues(clock, incident) if d.key == key), None)
    if due is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That clock has no such milestone.")
    return incident, clock, due


@router.post("/{clock_id}/milestones/{key}/met", response_model=ClockOut)
async def mark_met(
    incident_id: uuid.UUID,
    clock_id: uuid.UUID,
    key: str,
    body: MilestoneMet,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ClockOut:
    """Record that a milestone was submitted: by whom, when, and under what reference."""
    incident, clock, due = await _marking(db, user, incident_id, clock_id, key, body.version)
    mark = service.marks(clock)[key]
    if mark.met_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That milestone is already marked met.")
    now = utcnow()
    met_at = body.met_at or now
    _past(met_at, "When it was submitted")
    if met_at < service.aware_at(clock, incident):
        raise _unprocessable("It can't have been submitted before you became aware of the incident.")
    mark.met_at, mark.met_by, mark.recorded_at = met_at, user.id, now
    mark.reference, mark.note = body.reference.strip(), body.note.strip()
    clock.version += 1
    late = due.due is not None and met_at > due.due
    when = f"{regimes.remaining_words(met_at - due.due)} late" if late and due.due else "in time"
    reference = f" (reference {mark.reference})" if mark.reference else ""
    await activity.record(
        db,
        incident,
        user,
        "clock_milestone_met",
        f"{user.name} marked {label(clock)}: {due.name.lower()} met at {met_at:%d %b %H:%M} UTC, "
        f"{when}{reference}",
        entity_type="clock",
        entity_id=clock.id,
        milestone=key,
        reference=mark.reference,
        met_at=met_at.isoformat(),
    )
    audit.record(
        db,
        "clock_milestone_met",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        incident=incident.ref,
        clock=clock.ref,
        milestone=key,
        reference=mark.reference,
        met_at=met_at.isoformat(),
    )
    # A milestone that counts from this one now has its real start
    service.settle_nudges(clock, incident, now)
    await activity.finish(db, user)
    return await service.clock_out(db, user, incident, clock, utcnow())


@router.post("/{clock_id}/milestones/{key}/unmet", response_model=ClockOut)
async def mark_unmet(
    incident_id: uuid.UUID,
    clock_id: uuid.UUID,
    key: str,
    body: Versioned,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ClockOut:
    """Undo a milestone marked met by mistake. The record of both stays on the activity log."""
    incident, clock, due = await _marking(db, user, incident_id, clock_id, key, body.version)
    mark = service.marks(clock)[key]
    if mark.met_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That milestone isn't marked met.")
    was = mark.reference
    mark.met_at = mark.met_by = mark.recorded_at = None
    mark.reference, mark.note = "", ""
    clock.version += 1
    service.settle_nudges(clock, incident, utcnow())
    await activity.record(
        db,
        incident,
        user,
        "clock_milestone_unmet",
        f"{user.name} took back {label(clock)}: {due.name.lower()} met" + (f" (was {was})" if was else ""),
        entity_type="clock",
        entity_id=clock.id,
        milestone=key,
    )
    audit.record(
        db,
        "clock_milestone_unmet",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        incident=incident.ref,
        clock=clock.ref,
        milestone=key,
    )
    await activity.finish(db, user)
    return await service.clock_out(db, user, incident, clock, utcnow())


# ---- across incidents --------------------------------------------------------------------------------


@mine_router.get("/regimes", response_model=list[RegimeOut])
async def list_regimes(_: User = Depends(current_user)) -> list[RegimeOut]:
    """The built-in regimes and their milestones, for the form that adds a clock."""
    out = []
    for regime in regimes.REGIMES.values():
        counted = [
            " or ".join(f"{lim.span.words()} from {_basis_words(lim.basis)}" for lim in m.limits)
            for m in regime.milestones
        ]
        out.append(
            RegimeOut(
                key=RegimeKey(regime.key),
                name=regime.name,
                authority=regime.authority,
                timezone=regime.zone,
                source=regime.source,
                custom=regime.custom,
                needs_classification=regimes.needs_classification(regime.milestones),
                milestones=[
                    RegimeMilestoneOut(key=m.key, name=m.name, counted=c)
                    for m, c in zip(regime.milestones, counted, strict=True)
                ],
            )
        )
    return out


def _basis_words(basis: regimes.Basis) -> str:
    return {
        regimes.Basis.AWARE: "awareness",
        regimes.Basis.CLASSIFIED: "classification",
        regimes.Basis.PREVIOUS: "the report before",
    }[basis]


@mine_router.get("/mine", response_model=list[MyDeadline])
async def my_deadlines(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[MyDeadline]:
    """The next unmet milestone on each running clock you own, or on an incident you command.
    Soonest first."""
    commands = (
        select(IncidentMember.incident_id)
        .where(IncidentMember.user_id == user.id, IncidentMember.role == IncidentRole.COMMANDER)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(Clock, Incident)
            .join(Incident, Incident.id == Clock.incident_id)
            .where(
                Clock.closed_at.is_(None),
                access.visible_filter(user),
                or_(Clock.owner_id == user.id, Clock.incident_id.in_(commands)),
            )
        )
    ).all()
    now = utcnow()
    names = await views.names(db, [clock.owner_id for clock, _ in rows])
    out = []
    for clock, incident in rows:
        marks = service.marks(clock)
        for due in service.dues(clock, incident):
            mark = marks.get(due.key)
            if (mark and mark.met_at) or due.due is None or due.starts is None:
                continue
            out.append(
                MyDeadline(
                    clock_id=clock.id,
                    clock_ref=clock.ref,
                    regime_name=service.regime_of(clock).name,
                    title=for_viewer(user, clock.title),
                    key=due.key,
                    name=due.name,
                    due_at=due.due,
                    starts_at=due.starts,
                    provisional=due.provisional,
                    state=regimes.state(due, None, now),
                    owner=views.person(names, clock.owner_id),
                    as_owner=clock.owner_id == user.id,
                    incident=ClockIncident(
                        id=incident.id,
                        ref=incident.ref,
                        title=for_viewer(user, incident.title),
                        severity=incident.severity,
                        tlp=incident.tlp,
                    ),
                )
            )
            break  # the next one only; the rest are on the incident
    return sorted(out, key=lambda d: d.due_at)
