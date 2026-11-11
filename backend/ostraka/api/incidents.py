"""Incidents: declaring, listing, editing, the people and teams on them, and their records over time."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import licence, notify, workflow
from ostraka.api import playbooks
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incident_schemas import (
    ActivityOut,
    ChainOut,
    IncidentIn,
    IncidentOut,
    IncidentPatch,
    IncidentSummary,
    MemberIn,
    TeamAdd,
    TimelineItem,
)
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer, hides_iocs
from ostraka.incidents.refs import Ref, parse_ref
from ostraka.models import (
    PHASES_OF,
    ActivityEvent,
    Asset,
    AssetLink,
    Finding,
    Incident,
    IncidentCounter,
    IncidentMember,
    IncidentObservable,
    IncidentRole,
    IncidentStatus,
    IncidentTeam,
    NotificationKind,
    Observable,
    Playbook,
    RecordKind,
    ReportBrand,
    Role,
    Sighting,
    Task,
    Team,
    User,
    utcnow,
)

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


async def _next_number(db: AsyncSession, kind: RecordKind, year: int) -> int:
    await db.execute(insert(IncidentCounter).values(kind=kind, year=year, last=0).on_conflict_do_nothing())
    counter = await db.scalar(
        select(IncidentCounter)
        .where(IncidentCounter.kind == kind, IncidentCounter.year == year)
        .with_for_update()
    )
    counter.last += 1
    return counter.last


async def open_record(db: AsyncSession, user: User, body: IncidentIn, team_ids: set[uuid.UUID]) -> Incident:
    """Declare an incident or start a hunt, with its playbooks. Whoever opens it is its commander.
    Written with the caller's commit."""
    if user.role is Role.OBSERVER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Observers can't declare incidents or start hunts.")
    if user.team_id:
        team_ids.add(user.team_id)
    if team_ids:
        found = set((await db.scalars(select(Team.id).where(Team.id.in_(team_ids)))).all())
        if missing := team_ids - found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No such team: {', '.join(map(str, missing))}.")

    now = utcnow()
    incident = Incident(
        kind=body.kind,
        year=now.year,
        number=await _next_number(db, body.kind, now.year),
        title=body.title.strip(),
        summary=body.summary.strip(),
        severity=body.severity,
        tlp=body.tlp,
        phase=PHASES_OF[body.kind][0],
        tags=body.tags,
        declared_by=user.id,
        declared_at=now,
        aware_at=body.aware_at or now,
        updated_at=now,
    )
    incident.teams = [IncidentTeam(team_id=t) for t in team_ids]
    incident.members = [IncidentMember(user_id=user.id, role=IncidentRole.COMMANDER)]
    db.add(incident)
    await db.flush()
    verb = "started" if body.kind is RecordKind.HUNT else "declared"
    await activity.record(
        db,
        incident,
        user,
        "declared",
        f"{user.name} {verb} {incident.ref}: {incident.title} "
        f"({incident.severity.value}, TLP:{incident.tlp.value.upper()})",
    )
    for playbook_id in dict.fromkeys(body.playbook_ids):
        playbook = await db.get(Playbook, playbook_id)
        if playbook is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such playbook.")
        await playbooks.apply(db, incident, playbook, user)
    return incident


async def find_by_ref(db: AsyncSession, parsed: Ref) -> Incident | None:
    return await db.scalar(
        select(Incident).where(
            Incident.kind == RecordKind(parsed.record),
            Incident.year == parsed.year,
            Incident.number == parsed.number,
        )
    )


@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
async def declare(
    body: IncidentIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> IncidentOut:
    """Declare an incident or start a hunt. Whoever opens it is its commander until they hand over."""
    incident = await open_record(db, user, body, set(body.team_ids))
    await activity.finish(db, user)
    await db.refresh(incident, ["teams", "members"])
    return await views.detail(db, user, incident)


@router.get("", response_model=list[IncidentSummary])
async def list_incidents(
    state: IncidentStatus | None = Query(default=IncidentStatus.OPEN, alias="status"),
    kind: RecordKind | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[IncidentSummary]:
    """Incidents and hunts you can see, most recently changed first."""
    query = access.visible(user).order_by(Incident.updated_at.desc())
    if state is not None:
        query = query.where(Incident.status == state)
    if kind is not None:
        query = query.where(Incident.kind == kind)
    return [views.summary(i) for i in (await db.scalars(query)).unique().all()]


@router.get("/by-ref/{ref}", response_model=IncidentOut)
async def by_ref(ref: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    parsed = parse_ref(ref)
    if not parsed or parsed.kind != "incident":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such incident.")
    incident = await find_by_ref(db, parsed)
    if incident is None or not access.can_view(user, incident):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such incident.")
    return await views.detail(db, user, incident)


@router.get("/{incident_id}", response_model=IncidentOut)
async def get_incident(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> IncidentOut:
    return await views.detail(db, user, await access.load(db, user, incident_id))


async def _choose_brand(db: AsyncSession, user: User, incident: Incident, brand_id: uuid.UUID | None) -> str:
    """Set the report brand an incident's exports use, and say so for the activity log."""
    access.require_manage(user, incident)
    if not (await licence.current(db)).brand_library:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED, "Choosing a brand per incident is part of the Services edition."
        )
    brand = await db.get(ReportBrand, brand_id) if brand_id else None
    if brand_id and brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That report brand doesn't exist.")
    incident.brand_id = brand_id
    return f"report brand → {brand.name if brand else 'the default'}"


@router.patch("/{incident_id}", response_model=IncidentOut)
async def update_incident(
    incident_id: uuid.UUID,
    body: IncidentPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    incident = await access.load(db, user, incident_id, edit=True)
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if {"tlp", "status", "sensitive"} & changes.keys():
        access.require_manage(user, incident)
    if body.phase is not None and body.phase not in PHASES_OF[incident.kind]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"A {incident.kind.value} moves through {', '.join(p.value for p in PHASES_OF[incident.kind])}.",
        )
    views.check_version(incident.version, body.version, "incident")

    described = []
    if "brand_id" in changes:
        brand_id = changes.pop("brand_id")
        if brand_id != incident.brand_id:
            described.append(await _choose_brand(db, user, incident, brand_id))
    phase_moved = False
    for field, value in changes.items():
        if value is None or getattr(incident, field) == value:
            continue
        phase_moved = phase_moved or field == "phase"
        before = getattr(incident, field)
        setattr(incident, field, value)
        if field in ("title", "summary", "tags"):
            described.append(f"changed the {field}")
        else:
            shown = lambda v: v.value if hasattr(v, "value") else v  # noqa: E731
            described.append(f"{field.replace('_', ' ')} {shown(before)} → {shown(value)}")
    if "status" in changes:
        incident.closed_at = utcnow() if incident.status is IncidentStatus.CLOSED else None
    if described:
        incident.version += 1
        incident.updated_at = utcnow()
        await activity.record(
            db,
            incident,
            user,
            "incident_updated",
            f"{user.name} {'; '.join(described)}",
            changes={k: str(v) for k, v in changes.items() if k not in ("summary",)},
        )
        if phase_moved:
            await workflow.phase_changed(db, incident, user)
        await activity.finish(db, user)
    return await views.detail(db, user, incident)


# ---- teams and people -----------------------------------------------------------------------------


@router.post("/{incident_id}/teams", response_model=IncidentOut)
async def add_team(
    incident_id: uuid.UUID,
    body: TeamAdd,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    incident = await access.load(db, user, incident_id)
    access.require_manage(user, incident)
    team = await db.get(Team, body.team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such team.")
    if all(t.team_id != team.id for t in incident.teams):
        incident.teams.append(IncidentTeam(team_id=team.id))
        await activity.record(
            db,
            incident,
            user,
            "team_added",
            f"{user.name} brought in {team.name}",
            entity_type="team",
            entity_id=team.id,
        )
        await activity.finish(db, user)
        await db.refresh(incident, ["teams"])
    return await views.detail(db, user, incident)


@router.delete("/{incident_id}/teams/{team_id}", response_model=IncidentOut)
async def remove_team(
    incident_id: uuid.UUID,
    team_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    incident = await access.load(db, user, incident_id)
    access.require_manage(user, incident)
    link = next((t for t in incident.teams if t.team_id == team_id), None)
    if link is not None:
        name = link.team.name
        incident.teams.remove(link)
        await activity.record(
            db,
            incident,
            user,
            "team_removed",
            f"{user.name} stood down {name}",
            entity_type="team",
            entity_id=team_id,
        )
        await activity.finish(db, user)
        await db.refresh(incident, ["teams"])
    return await views.detail(db, user, incident)


@router.put("/{incident_id}/members/{user_id}", response_model=IncidentOut)
async def set_member(
    incident_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    """Add someone by name, or give them a role. Making someone commander hands over: the previous
    commander stays on the incident as a responder."""
    incident = await access.load(db, user, incident_id)
    access.require_manage(user, incident)
    target = await db.get(User, user_id)
    if target is None or not target.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person.")
    if body.role is not IncidentRole.RESPONDER and target.role is Role.OBSERVER:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Observers can follow an incident but not hold a role."
        )

    member = next((m for m in incident.members if m.user_id == target.id), None)
    if body.role is IncidentRole.COMMANDER:
        for m in incident.members:
            if m.role is IncidentRole.COMMANDER and m.user_id != target.id:
                m.role = IncidentRole.RESPONDER
        await db.flush()  # the one-commander index must see the old commander stand down first
    if member is None:
        incident.members.append(IncidentMember(user_id=target.id, role=body.role))
        summary = f"{user.name} added {target.name}" + (
            f" as {body.role.value}" if body.role is not IncidentRole.RESPONDER else ""
        )
    elif member.role is not body.role:
        member.role = body.role
        summary = f"{user.name} made {target.name} {body.role.value}"
    else:
        return await views.detail(db, user, incident)
    action = "commander_handover" if body.role is IncidentRole.COMMANDER else "member_set"
    await activity.record(
        db, incident, user, action, summary, entity_type="user", entity_id=target.id, role=body.role.value
    )
    if body.role is IncidentRole.COMMANDER:
        notify.send(
            db,
            [target],
            NotificationKind.COMMANDER,
            f"You're incident commander of {incident.ref}: {incident.title}",
            body=f"{user.name} handed command to you.",
            link=notify.incident_link(incident),
            incident=incident,
            actor=user,
        )
    await activity.finish(db, user)
    await db.refresh(incident, ["members"])
    return await views.detail(db, user, incident)


@router.delete("/{incident_id}/members/{user_id}", response_model=IncidentOut)
async def remove_member(
    incident_id: uuid.UUID,
    user_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    incident = await access.load(db, user, incident_id)
    access.require_manage(user, incident)
    member = next((m for m in incident.members if m.user_id == user_id), None)
    if member is not None:
        if member.role is IncidentRole.COMMANDER:
            raise HTTPException(status.HTTP_409_CONFLICT, "Hand over command to someone else first.")
        name = member.user.name
        incident.members.remove(member)
        await activity.record(
            db,
            incident,
            user,
            "member_removed",
            f"{user.name} removed {name}",
            entity_type="user",
            entity_id=user_id,
        )
        await activity.finish(db, user)
        await db.refresh(incident, ["members"])
    return await views.detail(db, user, incident)


# ---- the record -----------------------------------------------------------------------------------


@router.get("/{incident_id}/activity", response_model=list[ActivityOut])
async def get_activity(
    incident_id: uuid.UUID,
    after: int = Query(default=0, ge=0, description="Only events after this position"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[ActivityOut]:
    """What the responders did, in order. The attack itself is in the timeline."""
    await access.load(db, user, incident_id)
    events = (
        await db.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.incident_id == incident_id, ActivityEvent.position > after)
            .order_by(ActivityEvent.position)
        )
    ).all()
    return [
        ActivityOut(
            position=e.position,
            at=e.at,
            actor=views.Person(id=e.actor.id, name=e.actor.name) if e.actor else None,
            action=e.action,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            summary=for_viewer(user, e.summary),
            data={} if hides_iocs(user) else e.data,
        )
        for e in events
    ]


@router.get("/{incident_id}/activity/verify", response_model=ChainOut)
async def verify_activity(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> ChainOut:
    await access.load(db, user, incident_id)
    check = await activity.verify(db, incident_id)
    return ChainOut(ok=check.ok, events=check.events, broken_at=check.broken_at)


@router.get("/{incident_id}/timeline", response_model=list[TimelineItem])
async def timeline(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[TimelineItem]:
    """The attack in the order it happened, from sightings, findings and asset links that carry a
    time. Each item also says when it was recorded, so the two orders can be compared."""
    await access.load(db, user, incident_id)
    assets = {
        a.id: a for a in (await db.scalars(select(Asset).where(Asset.incident_id == incident_id))).all()
    }
    hostname = lambda asset_id, host="": (assets[asset_id].hostname if asset_id in assets else host) or None  # noqa: E731
    masked = hides_iocs(user)
    items: list[TimelineItem] = []

    rows = await db.execute(
        select(Sighting, IncidentObservable, Observable)
        .join(IncidentObservable, IncidentObservable.id == Sighting.incident_observable_id)
        .join(Observable, Observable.id == IncidentObservable.observable_id)
        .where(IncidentObservable.incident_id == incident_id, Sighting.seen_at.is_not(None))
    )
    for sighting, item, observable in rows.all():
        label = observable.kind.value.replace("_", " ")
        items.append(
            TimelineItem(
                at=sighting.seen_at,
                recorded_at=sighting.recorded_at,
                kind="sighting",
                ref=f"OBS-{observable.seq}",
                title=f"{label.capitalize()} seen" if masked else f"{observable.value} seen",
                detail=" · ".join(x for x in (sighting.source, "" if masked else sighting.user_account) if x),
                entity_id=item.id,
                asset=hostname(sighting.asset_id, sighting.host),
                techniques=[],
            )
        )
    for finding in (
        await db.scalars(
            select(Finding).where(Finding.incident_id == incident_id, Finding.occurred_at.is_not(None))
        )
    ).all():
        items.append(
            TimelineItem(
                at=finding.occurred_at,
                recorded_at=finding.created_at,
                kind="finding",
                ref=f"FND-{finding.seq}",
                title=for_viewer(user, finding.title),
                detail=finding.status.value,
                entity_id=finding.id,
                asset=hostname(finding.asset_id),
                techniques=finding.techniques or [],
            )
        )
    for link in (
        await db.scalars(
            select(AssetLink).where(AssetLink.incident_id == incident_id, AssetLink.seen_at.is_not(None))
        )
    ).all():
        src, dst = hostname(link.source_id) or "?", hostname(link.target_id) or "?"
        items.append(
            TimelineItem(
                at=link.seen_at,
                recorded_at=link.created_at,
                kind="link",
                ref=link.kind.value,
                title=f"{link.kind.value.replace('_', ' ').capitalize()}: {src} → {dst}",
                detail=for_viewer(user, link.note),
                entity_id=link.id,
                asset=src,
                techniques=[link.technique] if link.technique else [],
            )
        )
    return sorted(items, key=lambda i: (i.at, i.recorded_at))


# ---- references ------------------------------------------------------------------------------------

refs_router = APIRouter(prefix="/api/refs", tags=["incidents"])


@refs_router.get("/{ref}")
async def resolve_ref(
    ref: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> dict:
    """Where a #reference points, if you can see it."""
    parsed = parse_ref(ref)
    if parsed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a reference Ostraka knows.")
    title, incident, entity_id = "", None, None
    if parsed.kind == "incident":
        incident = await find_by_ref(db, parsed)
        title = incident.title if incident else ""
    elif parsed.kind in ("finding", "asset", "task"):
        model = {"finding": Finding, "asset": Asset, "task": Task}[parsed.kind]
        row = await db.scalar(select(model).where(model.seq == parsed.number))
        if row is not None:
            incident, entity_id = await db.get(Incident, row.incident_id), row.id
            title = row.hostname if parsed.kind == "asset" else row.title
    else:  # an observable spans incidents: resolve within those the viewer can see
        row = await db.scalar(
            select(IncidentObservable)
            .join(Observable, Observable.id == IncidentObservable.observable_id)
            .join(Incident, Incident.id == IncidentObservable.incident_id)
            .where(Observable.seq == parsed.number, access.visible_filter(user))
            .order_by(IncidentObservable.added_at.desc())
            .limit(1)
        )
        if row is not None:
            incident, entity_id = await db.get(Incident, row.incident_id), row.id
            title = row.observable.kind.value.replace("_", " ") if hides_iocs(user) else row.observable.value
    if incident is None or not access.can_view(user, incident):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found, or not an incident you can see.")
    return {
        "ref": parsed.text,
        "kind": parsed.kind,
        "incident_id": str(incident.id),
        "incident_ref": incident.ref,
        "entity_id": str(entity_id) if entity_id else None,
        "title": for_viewer(user, title),
    }
