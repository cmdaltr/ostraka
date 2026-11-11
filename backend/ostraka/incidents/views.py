"""Turning incident records into API responses: names for ids, masking, counts, cross-incident overlap."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.incident_schemas import (
    Elsewhere,
    FindingOut,
    IncidentObservableOut,
    IncidentOut,
    IncidentSummary,
    MemberOut,
    Person,
    SightingOut,
    TeamRef,
    VerificationOut,
)
from ostraka.incidents import access
from ostraka.incidents.masking import for_viewer, hides_iocs
from ostraka.models import (
    FINISHED,
    Asset,
    Comment,
    Finding,
    Incident,
    IncidentObservable,
    IncidentRole,
    IncidentTeam,
    Role,
    Task,
    Team,
    User,
)


async def names(db: AsyncSession, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = await db.execute(select(User.id, User.name).where(User.id.in_(wanted)))
    return dict(rows.all())


def person(lookup: dict[uuid.UUID, str], user_id: uuid.UUID | None) -> Person | None:
    return Person(id=user_id, name=lookup.get(user_id, "Former member")) if user_id else None


def check_version(current: int, sent: int, what: str) -> None:
    if current != sent:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Someone else changed this {what} while you were editing. "
            "Reload to see their change, then redo yours.",
        )


def summary(incident: Incident) -> IncidentSummary:
    commander = next((m for m in incident.members if m.role is IncidentRole.COMMANDER), None)
    return IncidentSummary(
        id=incident.id,
        kind=incident.kind,
        ref=incident.ref,
        title=incident.title,
        severity=incident.severity,
        tlp=incident.tlp,
        phase=incident.phase,
        status=incident.status,
        commander=Person(id=commander.user.id, name=commander.user.name) if commander else None,
        teams=sorted((TeamRef(id=t.team.id, name=t.team.name) for t in incident.teams), key=lambda t: t.name),
        declared_at=incident.declared_at,
        updated_at=incident.updated_at,
        tags=incident.tags or [],
    )


async def detail(db: AsyncSession, user: User, incident: Incident) -> IncidentOut:
    counts = {}
    for key, model in (("observables", IncidentObservable), ("assets", Asset), ("findings", Finding)):
        counts[key] = await db.scalar(select(func.count()).where(model.incident_id == incident.id)) or 0
    counts["tasks"] = (
        await db.scalar(
            select(func.count()).where(Task.incident_id == incident.id, Task.status.not_in(FINISHED))
        )
        or 0
    )
    counts["comments"] = (
        await db.scalar(
            select(func.count()).where(Comment.incident_id == incident.id, Comment.deleted_at.is_(None))
        )
        or 0
    )
    base = summary(incident)
    order = list(IncidentRole)
    return IncidentOut(
        **base.model_dump(),
        summary=for_viewer(user, incident.summary),
        aware_at=incident.aware_at,
        closed_at=incident.closed_at,
        members=[
            MemberOut(user=Person(id=m.user.id, name=m.user.name), role=m.role)
            for m in sorted(incident.members, key=lambda m: (order.index(m.role), m.user.name))
        ],
        version=incident.version,
        can_edit=access.can_edit(user, incident),
        can_manage=access.can_manage(user, incident),
        sensitive=incident.sensitive,
        can_export=await access.export_refusal(db, user, incident) is None,
        brand_id=incident.brand_id,
        counts=counts,
    )


async def elsewhere(
    db: AsyncSession, user: User, incident_id: uuid.UUID, observable_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Elsewhere]:
    """For each observable, the other incidents it appears in. Those the viewer can't see are only
    counted, with the teams (and their leads) to ask, so the overlap surfaces without leaking."""
    result: dict[uuid.UUID, Elsewhere] = {oid: Elsewhere() for oid in observable_ids}
    if not observable_ids:
        return result
    rows = (
        await db.execute(
            select(IncidentObservable.observable_id, Incident)
            .join(Incident, Incident.id == IncidentObservable.incident_id)
            .where(IncidentObservable.observable_id.in_(observable_ids), Incident.id != incident_id)
        )
    ).all()
    hidden_teams: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for observable_id, other in rows:
        entry = result[observable_id]
        if access.can_view(user, other):
            entry.visible.append({"id": str(other.id), "ref": other.ref, "title": other.title})
        else:
            entry.hidden += 1
            hidden_teams[observable_id].update(t.team_id for t in other.teams)

    team_ids = set().union(*hidden_teams.values()) if hidden_teams else set()
    if team_ids:
        teams = dict((await db.execute(select(Team.id, Team.name).where(Team.id.in_(team_ids)))).all())
        leads: dict[uuid.UUID, list[str]] = defaultdict(list)
        for team_id, name in (
            await db.execute(
                select(User.team_id, User.name).where(
                    User.team_id.in_(team_ids), User.role == Role.TEAM_LEAD, User.is_active.is_(True)
                )
            )
        ).all():
            leads[team_id].append(name)
        for observable_id, ids in hidden_teams.items():
            result[observable_id].ask = sorted(
                f"{teams[t]} (lead: {', '.join(sorted(leads[t]))})" if leads[t] else teams[t]
                for t in ids
                if t in teams
            )
    return result


def observable_out(user: User, item: IncidentObservable, seen_elsewhere: Elsewhere) -> IncidentObservableOut:
    masked = hides_iocs(user)
    return IncidentObservableOut(
        id=item.id,
        ref=f"OBS-{item.observable.seq}",
        kind=item.observable.kind,
        value=None if masked else item.observable.value,
        masked=masked,
        verdict=item.verdict,
        note=for_viewer(user, item.note),
        tags=item.tags or [],
        sightings=[
            SightingOut(
                id=s.id,
                seen_at=s.seen_at,
                asset_id=s.asset_id,
                host=s.host,
                user_account="" if masked else s.user_account,
                source=s.source,
                recorded_by=s.recorded_by,
                recorded_at=s.recorded_at,
            )
            for s in item.sightings
        ],
        added_at=item.added_at,
        version=item.version,
        elsewhere=seen_elsewhere,
    )


async def finding_out(db: AsyncSession, user: User, finding: Finding) -> FindingOut:
    lookup = await names(
        db, [finding.created_by, finding.decided_by, *(v.ran_by for v in finding.verifications)]
    )
    return FindingOut(
        id=finding.id,
        ref=f"FND-{finding.seq}",
        kind=finding.kind,
        title=for_viewer(user, finding.title),
        statement=for_viewer(user, finding.statement),
        status=finding.status,
        occurred_at=finding.occurred_at,
        asset_id=finding.asset_id,
        techniques=finding.techniques or [],
        tags=finding.tags or [],
        observable_ids=[link.incident_observable_id for link in finding.observable_links],
        source_reliability=finding.source_reliability,
        information_credibility=finding.information_credibility,
        handling_code=finding.handling_code,
        handling_conditions=finding.handling_conditions,
        created_by=person(lookup, finding.created_by),
        created_at=finding.created_at,
        decided_by=person(lookup, finding.decided_by),
        decided_at=finding.decided_at,
        decision_note=for_viewer(user, finding.decision_note),
        verifications=[
            VerificationOut(
                id=v.id,
                method=v.method,
                platform=v.platform,
                query=for_viewer(user, v.query),
                scope=v.scope,
                time_from=v.time_from,
                time_to=v.time_to,
                result_count=v.result_count,
                reference=v.reference,
                note=for_viewer(user, v.note),
                ran_by=person(lookup, v.ran_by),
                ran_at=v.ran_at,
            )
            for v in finding.verifications
        ],
        version=finding.version,
    )


async def team_names(db: AsyncSession, incident: Incident) -> list[str]:
    rows = await db.execute(
        select(Team.name)
        .join(IncidentTeam, IncidentTeam.team_id == Team.id)
        .where(IncidentTeam.incident_id == incident.id)
    )
    return sorted(rows.scalars().all())
