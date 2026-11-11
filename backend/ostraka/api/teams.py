"""Teams. Anyone signed in can list them; only admins create or rename them. Admins and a team's
own lead connect its Microsoft Teams channel."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit
from ostraka.auth import policy, sessions
from ostraka.auth.deps import current_user, require_role
from ostraka.auth.secrets import decrypt, encrypt
from ostraka.config import get_settings
from ostraka.db import get_session
from ostraka.incidents.views import check_version
from ostraka.models import Role, Team, User
from ostraka.notify import channels
from ostraka.schemas import TeamIn, TeamOut, TeamPatch
from ostraka.work_schemas import TeamWebhookIn

router = APIRouter(prefix="/api/teams", tags=["teams"])
admin_only = require_role(Role.ADMIN)


@router.get("", response_model=list[TeamOut])
async def list_teams(_: User = Depends(current_user), db: AsyncSession = Depends(get_session)) -> list[Team]:
    return list((await db.scalars(select(Team).order_by(Team.name))).all())


async def _save(db: AsyncSession, team: Team) -> Team:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A team with that name or code already exists."
        ) from exc
    await db.refresh(team)
    return team


@router.post("", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
async def create_team(
    body: TeamIn, request: Request, actor: User = Depends(admin_only), db: AsyncSession = Depends(get_session)
) -> Team:
    team = Team(
        name=body.name.strip(), code=body.code, aliases=body.aliases, description=body.description.strip()
    )
    db.add(team)
    audit.record(db, "team_created", actor_id=actor.id, ip=sessions.client_ip(request), name=team.name)
    return await _save(db, team)


@router.patch("/{team_id}", response_model=TeamOut)
async def update_team(
    team_id: uuid.UUID,
    body: TeamPatch,
    request: Request,
    actor: User = Depends(admin_only),
    db: AsyncSession = Depends(get_session),
) -> Team:
    team = await db.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such team.")
    check_version(team.version, body.version, "team")
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True, exclude={"version"}).items():
        setattr(team, field, value.strip() if isinstance(value, str) else value)
    team.version += 1
    audit.record(db, "team_changed", actor_id=actor.id, ip=sessions.client_ip(request), team=team.name)
    return await _save(db, team)


async def _channel_team(db: AsyncSession, actor: User, team_id: uuid.UUID) -> Team:
    team = await db.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such team.")
    if not policy.can_set_team_channel(actor, team.id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only admins and the team's own lead can change its channel."
        )
    return team


@router.put("/{team_id}/channel", response_model=TeamOut)
async def set_channel(
    team_id: uuid.UUID,
    body: TeamWebhookIn,
    request: Request,
    actor: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Team:
    """Connect the team's Teams channel with a Workflows webhook URL, or disconnect it with an empty one.
    The URL is stored encrypted and never shown again, since anyone holding it can post to the channel."""
    team = await _channel_team(db, actor, team_id)
    url = body.url.strip()
    if url and (problem := channels.teams_webhook_problem(url)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    team.teams_webhook = encrypt(url) if url else ""
    audit.record(
        db,
        "team_channel_set" if url else "team_channel_cleared",
        actor_id=actor.id,
        ip=sessions.client_ip(request),
        team=team.name,
    )
    return await _save(db, team)


@router.post("/{team_id}/channel/test", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def test_channel(
    team_id: uuid.UUID, actor: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> None:
    team = await _channel_team(db, actor, team_id)
    if not team.teams_webhook:
        raise HTTPException(status.HTTP_409_CONFLICT, "This team has no channel connected.")
    sent = await channels.send_teams(
        decrypt(team.teams_webhook),
        f"Ostraka is connected to {team.name}",
        f"{actor.name} sent this test. Tasks for {team.name}'s queue, and reminders about them, "
        "will appear here.",
        get_settings().public_url,
    )
    if not sent:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Teams didn't accept the message. Check the webhook URL."
        )
