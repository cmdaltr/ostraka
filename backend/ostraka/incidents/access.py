"""Who may see and change an incident.

An incident is visible to members of the teams involved, to people added to it by name, and to
admins. Observers may look but never change anything. People and roles are managed by the incident
commander, the leads of the teams involved, and admins.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import Select, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.auth import policy
from ostraka.models import ActivityEvent, Incident, IncidentMember, IncidentRole, IncidentTeam, Role, User


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


def can_edit(user: User, incident: Incident) -> bool:
    return user.role is not Role.OBSERVER and can_view(user, incident)


def commander_id(incident: Incident) -> uuid.UUID | None:
    return next((m.user_id for m in incident.members if m.role is IncidentRole.COMMANDER), None)


def can_manage(user: User, incident: Incident) -> bool:
    """Add or remove teams and people, assign roles, close the incident, change its TLP."""
    if user.role is Role.ADMIN or commander_id(incident) == user.id:
        return True
    return (
        user.role is Role.TEAM_LEAD
        and user.team_id is not None
        and any(t.team_id == user.team_id for t in incident.teams)
    )


async def load(db: AsyncSession, user: User, incident_id: uuid.UUID, *, edit: bool = False) -> Incident:
    """The incident, if `user` may see it (and change it, with `edit`). Otherwise 404 or 403:
    an incident you can't see is reported as missing, so its existence doesn't leak."""
    incident = await db.get(Incident, incident_id)
    if incident is None or not can_view(user, incident):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such incident.")
    if edit and not can_edit(user, incident):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Observers can read incidents but not change them.")
    return incident


def require_manage(user: User, incident: Incident) -> None:
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
