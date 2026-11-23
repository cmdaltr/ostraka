"""Who may see and change an incident.

An incident is visible to members of the teams involved, to people added to it by name, and to
admins. Observers may look but never change anything. People and roles are managed by the incident
commander, the leads of the teams involved, and admins.

An archived record is read only, and one in cold store shows only its catalogue entry and lessons
(`ostraka.archive`); both answer 423 until a restore is approved. The chain of custody is the exception:
an exhibit still changes hands after its record is archived, so custody passes `custody=True`.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import Select, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.auth import policy
from ostraka.models import (
    ActivityEvent,
    ArchiveState,
    Incident,
    IncidentMember,
    IncidentRole,
    IncidentTeam,
    Role,
    User,
)


def visible_filter(user: User):
    """A WHERE clause on Incident selecting what `user` may see."""
    if user.role is Role.ADMIN:
        return True
    by_team = exists().where(IncidentTeam.incident_id == Incident.id, IncidentTeam.team_id == user.team_id)
    by_name = exists().where(IncidentMember.incident_id == Incident.id, IncidentMember.user_id == user.id)
    return or_(by_team, by_name) if user.team_id else by_name


def visible(user: User) -> Select:
    return select(Incident).where(visible_filter(user))


def can_view(user: User, incident: Incident) -> bool:
    if user.role is Role.ADMIN:
        return True
    if user.team_id and any(t.team_id == user.team_id for t in incident.teams):
        return True
    return any(m.user_id == user.id for m in incident.members)


def can_edit(user: User, incident: Incident, *, custody: bool = False) -> bool:
    if not custody and incident.archive_state is not ArchiveState.ACTIVE:
        return False
    return user.role is not Role.OBSERVER and can_view(user, incident)


STATE_WORDS = {ArchiveState.ARCHIVED: "archived", ArchiveState.COLD: "in cold store"}


def locked(incident: Incident) -> HTTPException | None:
    """The answer to changing a record that's archived or in cold store, if it is."""
    if incident.archive_state is ArchiveState.ACTIVE:
        return None
    return HTTPException(
        status.HTTP_423_LOCKED,
        f"{incident.ref} is {STATE_WORDS[incident.archive_state]}, so it can't be changed. "
        "Ask for it to be restored first.",
    )


def sealed(incident: Incident) -> HTTPException:
    return HTTPException(
        status.HTTP_423_LOCKED,
        f"{incident.ref} is in cold store: its catalogue entry and lessons can be read, the rest can't. "
        "Ask for it to be restored to open it.",
    )


def commander_id(incident: Incident) -> uuid.UUID | None:
    return next((m.user_id for m in incident.members if m.role is IncidentRole.COMMANDER), None)


def can_manage(user: User, incident: Incident, *, custody: bool = False) -> bool:
    """Add or remove teams and people, assign roles, close the incident, change its TLP. Nobody does
    while it's archived."""
    if not custody and incident.archive_state is not ArchiveState.ACTIVE:
        return False
    if user.role is Role.ADMIN or commander_id(incident) == user.id:
        return True
    return (
        user.role is Role.TEAM_LEAD
        and user.team_id is not None
        and any(t.team_id == user.team_id for t in incident.teams)
    )


def manages_record(user: User, incident: Incident) -> bool:
    """Whether `user` would manage the record, archived or not: who may ask to archive it further."""
    return can_manage(user, incident, custody=True)


async def load(
    db: AsyncSession,
    user: User,
    incident_id: uuid.UUID,
    *,
    edit: bool = False,
    sealed_ok: bool = False,
    custody: bool = False,
) -> Incident:
    """The incident, if `user` may see it (and change it, with `edit`). Otherwise 404 or 403:
    an incident you can't see is reported as missing, so its existence doesn't leak. An archived record
    refuses `edit`, and one in cold store refuses everything but what passes `sealed_ok`, with 423;
    `custody` is let through both."""
    incident = await db.get(Incident, incident_id)
    if incident is None or not can_view(user, incident):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such incident.")
    if not custody:
        if incident.archive_state is ArchiveState.COLD and not sealed_ok:
            raise sealed(incident)
        if edit and (refused := locked(incident)):
            raise refused
    if edit and not can_edit(user, incident, custody=custody):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Observers can read incidents but not change them.")
    return incident


def require_manage(user: User, incident: Incident) -> None:
    if refused := locked(incident):
        raise refused
    if not can_manage(user, incident):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the incident commander, the leads of the teams involved, or an admin can do that.",
        )


async def worked_on(db: AsyncSession, user: User, incident: Incident) -> bool:
    """Whether `user` has worked the incident: holds a role in it, or has recorded something in it."""
    if any(m.user_id == user.id for m in incident.members):
        return True
    return bool(
        await db.scalar(
            select(
                exists().where(ActivityEvent.incident_id == incident.id, ActivityEvent.actor_id == user.id)
            )
        )
    )


async def export_refusal(db: AsyncSession, user: User, incident: Incident) -> str | None:
    """Why `user` may not export this incident's reports, or None if they may."""
    return policy.can_export(
        user,
        worked_on=await worked_on(db, user, incident),
        is_commander=commander_id(incident) == user.id,
        sensitive=incident.sensitive,
    )
