"""The live socket. A browser asks for a one-time ticket over HTTPS, then opens `/ws?ticket=…`.

Messages from the browser:
  {"type": "join", "incident": "<id>"}                       watch an incident
  {"type": "presence", "incident": "<id>", "focus": "finding:<id>", "editing": true}
  {"type": "leave", "incident": "<id>"}
Messages to the browser: {"type": "activity", "event": {...}}, {"type": "presence", "people": [...]}
and {"type": "notification"} (fetch /api/notifications).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ostraka import live
from ostraka.auth.deps import Caller, current_caller
from ostraka.db import sessions
from ostraka.incidents import access
from ostraka.log import get_logger
from ostraka.models import Incident, User

log = get_logger(__name__)
router = APIRouter(tags=["live"])


@router.post("/api/live/ticket")
async def ticket(caller: Caller = Depends(current_caller)) -> dict:
    """A ticket good for one connection within 30 seconds."""
    return {"ticket": await live.issue_ticket(caller.user.id)}


async def _can_join(user_id: str, incident_id: str) -> bool:
    try:
        incident_uuid = uuid.UUID(incident_id)
    except ValueError:
        return False
    async with sessions()() as db:
        user = await db.get(User, uuid.UUID(user_id))
        incident = await db.get(Incident, incident_uuid)
        return bool(user and user.is_active and incident and access.can_view(user, incident))


@router.websocket("/ws")
async def socket(websocket: WebSocket, ticket: str = "") -> None:
    user_id = await live.broker().take_ticket(ticket) if ticket else None
    if user_id is None:
        await websocket.close(code=4401, reason="Sign in again")
        return
    async with sessions()() as db:
        user = await db.get(User, uuid.UUID(user_id))
        name = user.name if user else "Someone"
    await websocket.accept()
    connection = uuid.uuid4().hex
    joined: set[str] = set()
    live.hub.users[user_id].add(websocket)

    async def presence(incident: str, gone: bool = False, focus: str = "", editing: bool = False) -> None:
        await live.publish_incident(
            incident,
            {
                "type": "presence-update",
                "connection": connection,
                "user_id": user_id,
                "name": name,
                "focus": focus,
                "editing": editing,
                "gone": gone,
            },
        )

    try:
        while True:
            message = await websocket.receive_json()
            incident = str(message.get("incident", ""))
            kind = message.get("type")
            if kind == "join":
                if not await _can_join(user_id, incident):
                    await websocket.send_json(
                        {"type": "error", "incident": incident, "detail": "No such incident."}
                    )
                    continue
                live.hub.rooms[incident].add(websocket)
                joined.add(incident)
                await presence(incident)
            elif kind == "presence" and incident in joined:
                await presence(
                    incident, focus=str(message.get("focus", ""))[:80], editing=bool(message.get("editing"))
                )
            elif kind == "leave" and incident in joined:
                live.hub.rooms[incident].discard(websocket)
                joined.discard(incident)
                await presence(incident, gone=True)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("live: socket for %s failed", user_id)
    finally:
        live.hub.users[user_id].discard(websocket)
        for incident in joined:
            live.hub.rooms[incident].discard(websocket)
            await presence(incident, gone=True)
