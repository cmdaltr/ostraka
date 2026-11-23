"""An incident's own chat channel, in Microsoft Teams or Slack.

The commander, the leads of the teams involved and admins connect it (the same people who manage
the incident). It hears about the record's turns (`notify.channel_words`), cut down as everything
leaving Ostraka is: indicators masked, and a TLP:RED or TLP:AMBER+STRICT record named by its
reference and code name only. The URL is stored encrypted and never shown again.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit, notify
from ostraka.auth import sessions
from ostraka.auth.deps import current_user
from ostraka.auth.secrets import decrypt, encrypt
from ostraka.db import get_session
from ostraka.incident_schemas import IncidentOut
from ostraka.incidents import access, activity, views
from ostraka.models import User
from ostraka.notify import channels
from ostraka.work_schemas import TeamWebhookIn

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


@router.put("/{incident_id}/channel", response_model=IncidentOut)
async def set_channel(
    incident_id: uuid.UUID,
    body: TeamWebhookIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    """Connect the incident's channel with a Teams Workflows or Slack incoming webhook, or disconnect
    it with an empty URL."""
    incident = await access.load(db, user, incident_id)
    access.require_manage(user, incident)
    url = body.url.strip()
    if url and (problem := channels.chat_webhook_problem(url)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    kind = channels.chat_kind(url) or ""
    was = incident.channel_kind
    incident.channel_webhook = encrypt(url) if url else ""
    incident.channel_kind = kind
    audit.record(
        db,
        "incident_channel_set" if url else "incident_channel_cleared",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        incident=incident.ref,
        **({"kind": kind} if url else {}),
    )
    if url:
        said = f"{user.name} connected a {channels.KINDS[kind]} channel" + (
            " in place of the last" if was else ""
        )
    else:
        said = f"{user.name} disconnected the {channels.KINDS.get(was, 'chat')} channel"
    await activity.record(db, incident, user, "channel_set" if url else "channel_cleared", said, kind=kind)
    await activity.finish(db, user)
    return await views.detail(db, user, incident)


@router.post("/{incident_id}/channel/test", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def test_channel(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> None:
    incident = await access.load(db, user, incident_id)
    access.require_manage(user, incident)
    if not incident.channel_webhook:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{incident.ref} has no channel connected.")
    title, text = notify.incident_post(
        incident,
        "Ostraka is connected",
        f"{user.name} sent this test. Phase, severity and TLP changes, confirmed findings, stakeholder "
        "updates, handovers and breached deadlines will appear here.",
    )
    sent = await channels.send_chat(
        decrypt(incident.channel_webhook), title, text, notify.link_url(notify.incident_link(incident))
    )
    if not sent:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"{channels.KINDS.get(incident.channel_kind, 'The channel')} didn't accept the message. "
            "Check the webhook URL.",
        )
