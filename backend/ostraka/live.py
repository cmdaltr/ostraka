"""Live updates: who is looking at an incident, and what just changed in it.

Browsers connect to `/ws` with a one-time ticket (never a JWT in the URL), join the incidents they
have open, and receive activity events and presence. Each socket also hears its own person's
notifications, so the bell updates on every page. Messages travel through Redis pub/sub so every
server process sees them; with `OSTRAKA_REDIS_URL=memory://` (tests) they stay in-process.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from redis.asyncio import Redis

from ostraka import busy
from ostraka.config import get_settings
from ostraka.log import get_logger

log = get_logger(__name__)

CHANNEL = "ostraka:incident:"
USER_CHANNEL = "ostraka:user:"
TICKET_SECONDS = 30
PRESENCE_SECONDS = 60  # a viewer who hasn't been heard from for this long has gone


class Broker:
    """In-process messaging and ticket store, used directly in tests and as Redis's local half."""

    def __init__(self) -> None:
        self._tickets: dict[str, tuple[str, float]] = {}

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        await hub.deliver(channel, message)

    async def put_ticket(self, ticket: str, user_id: str) -> None:
        self._tickets[ticket] = (user_id, time.monotonic() + TICKET_SECONDS)

    async def take_ticket(self, ticket: str) -> str | None:
        user_id, expires = self._tickets.pop(ticket, (None, 0.0))
        return user_id if user_id and expires > time.monotonic() else None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class RedisBroker(Broker):
    def __init__(self, url: str) -> None:
        super().__init__()
        self.redis = Redis.from_url(url, decode_responses=True)
        self._task: asyncio.Task | None = None

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        try:
            await self.redis.publish(channel, json.dumps(message, default=str))
        except Exception as exc:  # Redis down: still reach viewers on this process
            log.warning("live: redis publish failed (%s); delivering locally only", exc)
            await hub.deliver(channel, message)

    async def put_ticket(self, ticket: str, user_id: str) -> None:
        await self.redis.set(f"ostraka:ticket:{ticket}", user_id, ex=TICKET_SECONDS)

    async def take_ticket(self, ticket: str) -> str | None:
        return await self.redis.getdel(f"ostraka:ticket:{ticket}")

    async def start(self) -> None:
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.redis.aclose()

    async def _listen(self) -> None:
        while True:
            try:
                pubsub = self.redis.pubsub()
                await pubsub.psubscribe(f"{CHANNEL}*", f"{USER_CHANNEL}*")
                async for item in pubsub.listen():
                    if item["type"] == "pmessage":
                        await hub.deliver(item["channel"], json.loads(item["data"]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("live: redis subscription lost (%s); retrying", exc)
                await asyncio.sleep(2)


@dataclass
class Viewer:
    user_id: str
    name: str
    focus: str = ""  # e.g. "finding:<id>", what they have open
    editing: bool = False
    seen: float = field(default_factory=time.monotonic)


class Hub:
    """The sockets connected to this process, the incidents each has joined, and presence."""

    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self.users: dict[str, set[WebSocket]] = defaultdict(set)  # user id -> that person's sockets
        # incident id -> connection id -> viewer; fed by presence messages from every process
        self.presence: dict[str, dict[str, Viewer]] = defaultdict(dict)

    async def deliver(self, channel: str, message: dict[str, Any]) -> None:
        if channel.startswith(USER_CHANNEL):
            user = channel.removeprefix(USER_CHANNEL)
            for socket in list(self.users.get(user, ())):
                try:
                    await socket.send_json(message)
                except Exception:
                    self.users[user].discard(socket)
            return
        incident = channel.removeprefix(CHANNEL)
        if message.get("type") == "presence-update":
            self._apply_presence(incident, message)
            message = {"type": "presence", "incident": incident, "people": self.people(incident)}
        for socket in list(self.rooms.get(incident, ())):
            try:
                await socket.send_json(message)
            except Exception:
                self.rooms[incident].discard(socket)

    def _apply_presence(self, incident: str, message: dict[str, Any]) -> None:
        connection = message["connection"]
        if message.get("gone"):
            self.presence[incident].pop(connection, None)
        else:
            self.presence[incident][connection] = Viewer(
                message["user_id"], message["name"], message.get("focus", ""), bool(message.get("editing"))
            )

    def people(self, incident: str) -> list[dict[str, Any]]:
        now = time.monotonic()
        viewers = self.presence.get(incident, {})
        for connection in [c for c, v in viewers.items() if now - v.seen > PRESENCE_SECONDS]:
            viewers.pop(connection)
        merged: dict[str, dict[str, Any]] = {}
        for viewer in viewers.values():  # one entry per person, however many tabs
            entry = merged.setdefault(
                viewer.user_id,
                {"user_id": viewer.user_id, "name": viewer.name, "focus": viewer.focus, "editing": False},
            )
            entry["editing"] = entry["editing"] or viewer.editing
            if viewer.editing:
                entry["focus"] = viewer.focus
        return sorted(merged.values(), key=lambda p: p["name"])

    def focused(self, incident: str, focus: str) -> list[Viewer]:
        """Who has one object open in an incident right now, one entry per person."""
        now = time.monotonic()
        found: dict[str, Viewer] = {}
        for viewer in self.presence.get(incident, {}).values():
            if viewer.focus == focus and now - viewer.seen <= PRESENCE_SECONDS:
                if viewer.user_id not in found or viewer.editing:
                    found[viewer.user_id] = viewer
        return list(found.values())

    def anyone_editing(self) -> bool:
        now = time.monotonic()
        return any(
            v.editing and now - v.seen <= PRESENCE_SECONDS
            for room in self.presence.values()
            for v in room.values()
        )


hub = Hub()
_broker: Broker | None = None


def broker() -> Broker:
    global _broker
    if _broker is None:
        url = get_settings().redis_url
        _broker = Broker() if url.startswith("memory://") else RedisBroker(url)
    return _broker


async def startup() -> None:
    await broker().start()
    # A rebuild would cut off someone mid-edit; homelab waits while anyone is editing
    busy.register("live-editing", hub.anyone_editing)


async def shutdown() -> None:
    await broker().stop()


async def publish_incident(incident_id: uuid.UUID | str, message: dict[str, Any]) -> None:
    await broker().publish(f"{CHANNEL}{incident_id}", message)


async def publish_user(user_id: uuid.UUID | str, message: dict[str, Any]) -> None:
    """To one person's open tabs. Only signals go this way; the content is fetched through the API."""
    await broker().publish(f"{USER_CHANNEL}{user_id}", message)


async def issue_ticket(user_id: uuid.UUID) -> str:
    ticket = secrets.token_urlsafe(24)
    await broker().put_ticket(ticket, str(user_id))
    return ticket
