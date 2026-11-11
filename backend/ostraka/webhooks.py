"""Webhooks: ostraka POSTs JSON to URLs an admin registers when something happens in an incident or
hunt. This is the `automation` feature (Pro and Services), built to the design shared with Shikari.

Events, each read from the activity log after its commit:
    incident.created         an incident was declared
    incident.status_changed  an incident moved phase, or was reopened
    incident.closed          an incident was closed
    task.assigned            a task was given to a person or a team, or claimed
    hunt.completed           a hunt was closed

Each delivery is {"event", "sent_at", "data"}, with X-Ostraka-Event and, when the webhook has a
secret, X-Ostraka-Signature: sha256=<hex HMAC-SHA256 of the raw body>, so the receiver can check it
came from this server. Three tries with backoff; the last KEEP_DELIVERIES are kept per webhook.

`data` goes through `notify.outward_data` like every other message that leaves ostraka:
indicators masked, and a TLP:RED or TLP:AMBER+STRICT record named by its reference only.

The licence is checked when a webhook is sent, never when it's saved: a Free installation keeps
its webhooks, and they start firing again when a licence with `automation` is installed.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import busy, licence, notify
from ostraka.auth.secrets import decrypt
from ostraka.db import sessions
from ostraka.log import get_logger
from ostraka.models import (
    ActivityEvent,
    Incident,
    IncidentStatus,
    RecordKind,
    Task,
    Team,
    User,
    Webhook,
    WebhookDelivery,
    utcnow,
)
from ostraka.notify import channels

log = get_logger(__name__)

EVENTS = ("incident.created", "incident.status_changed", "incident.closed", "task.assigned", "hunt.completed")
KEEP_DELIVERIES = 20
TRIES = 3
BACKOFF = 2.0  # seconds, times the attempt number
_TASK_ACTIONS = frozenset({"task_added", "task_updated", "task_claimed"})
_ACTIONS = frozenset({"declared", "incident_updated"}) | _TASK_ACTIONS
_background: set[asyncio.Task] = set()
# RFC 1918. ipaddress's is_private also counts documentation and other reserved ranges.
_PRIVATE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def url_problem(url: str) -> str | None:
    """Why a webhook can't use this URL, or None. https, except http to this machine or a private
    network, where receivers often have no certificate. Link-local addresses (cloud metadata) never."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return "The URL must start with https://."
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and address.is_link_local:
        return "Ostraka doesn't send webhooks to link-local addresses."
    if parts.scheme == "http":
        local = host == "localhost" or host.endswith(".localhost")
        nearby = address is not None and (address.is_loopback or any(address in n for n in _PRIVATE))
        if not (local or nearby):
            return "Use https://. Plain http:// is only allowed to localhost and private network addresses."
    return None


def events_problem(events: list[str]) -> str | None:
    if not events or set(events) - set(EVENTS):
        return f"Choose events from {', '.join(EVENTS)}."
    return None


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def events_for(action: str, data: dict[str, Any], kind: RecordKind) -> list[str]:
    """The webhook events one activity event raises. Pure, so the mapping is tested on its own."""
    if kind is RecordKind.HUNT:
        closed = (
            action == "incident_updated" and data.get("changes", {}).get("status") == IncidentStatus.CLOSED
        )
        return ["hunt.completed"] if closed else []
    if action == "declared":
        return ["incident.created"]
    if action == "incident_updated":
        changes = data.get("changes", {})
        if changes.get("status") == IncidentStatus.CLOSED:
            return ["incident.closed"]
        if "phase" in changes or changes.get("status") == IncidentStatus.OPEN:
            return ["incident.status_changed"]
        return []
    if action in ("task_added", "task_claimed") or (action == "task_updated" and data.get("reassigned")):
        return ["task.assigned"]
    return []


@dataclass(frozen=True)
class _Happened:
    incident_id: uuid.UUID
    action: str
    data: dict[str, Any]
    entity_id: uuid.UUID | None


def queue(events: list[ActivityEvent]) -> None:
    """After a commit (`activity.finish`): send whatever these events raise, in the background. A
    change that was rolled back never gets here, so it sends nothing."""
    happened = [
        _Happened(e.incident_id, e.action, dict(e.data or {}), e.entity_id)
        for e in events
        if e.action in _ACTIONS
    ]
    if not happened:
        return
    job = asyncio.create_task(_send_all(happened))
    _background.add(job)
    job.add_done_callback(_background.discard)


async def wait() -> None:
    """Let in-flight deliveries finish (shutdown and tests)."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


def busy_sending() -> bool:
    return bool(_background)


async def _send_all(happened: list[_Happened]) -> None:
    try:
        async with sessions()() as db:
            if not (await licence.current(db)).has(licence.AUTOMATION):
                return
            hooks = (await db.scalars(select(Webhook).where(Webhook.enabled))).all()
            if not hooks:
                return
            for item in happened:
                incident = await db.get(Incident, item.incident_id)
                if incident is None:
                    continue
                for event in events_for(item.action, item.data, incident.kind):
                    targets = [h for h in hooks if event in h.events]
                    if not targets:
                        continue
                    data = notify.outward_data(incident, await _describe(db, incident, event, item))
                    if not notify.restricted(incident):
                        # Added after masking, which would take ostraka's own address for an indicator
                        data["url"] = notify.link_url(f"/incidents/{incident.ref}")
                    for hook in targets:
                        await deliver(db, hook, event, data)
            await db.commit()
    except Exception:
        log.exception("webhook delivery failed")


async def _describe(db: AsyncSession, incident: Incident, event: str, item: _Happened) -> dict[str, Any]:
    """What a webhook says about an event, before `notify.outward_data` cuts it down."""
    data: dict[str, Any] = {
        "ref": incident.ref,
        "kind": incident.kind.value,
        "title": incident.title,
        "severity": incident.severity.value,
        "tlp": incident.tlp.value,
        "phase": incident.phase.value,
        "status": incident.status.value,
    }
    if event == "task.assigned" and item.entity_id:
        task = await db.get(Task, item.entity_id)
        if task is not None:
            assignee = await db.get(User, task.assignee_id) if task.assignee_id else None
            team = await db.get(Team, task.team_id) if task.team_id else None
            data["task"] = {
                "ref": task.ref,
                "title": task.title,
                "assignee": assignee.name if assignee else None,
                "team": team.name if team else None,
                "due_at": task.due_at.isoformat() if task.due_at else None,
            }
    return data


def body_of(event: str, data: dict[str, Any], sent_at: datetime | None = None) -> bytes:
    return json.dumps(
        {"event": event, "sent_at": (sent_at or utcnow()).isoformat(), "data": data}, default=str
    ).encode()


async def deliver(
    db: AsyncSession, hook: Webhook, event: str, data: dict[str, Any], *, tries: int = TRIES
) -> WebhookDelivery:
    """POST one event to one webhook and keep the result. The caller commits."""
    body = body_of(event, data)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ostraka-webhook/1",
        "X-Ostraka-Event": event,
    }
    if hook.secret:
        headers["X-Ostraka-Signature"] = sign(decrypt(hook.secret), body)
    result = WebhookDelivery(
        id=uuid.uuid4(), webhook_id=hook.id, event=event, at=utcnow(), ok=False, error=""
    )
    for attempt in range(tries):
        result.tries = attempt + 1
        try:
            result.status = await channels.post(hook.url, content=body, headers=headers)
            if result.status < 300:
                result.ok, result.error = True, ""
                break
            result.error = f"HTTP {result.status}"
            if result.status < 500 and result.status != 429:
                break  # the receiver refused it; trying again won't change that
        except Exception as exc:  # noqa: BLE001 (any failure to reach the receiver is recorded, not raised)
            result.status, result.error = None, str(exc)[:200] or type(exc).__name__
        if attempt < tries - 1:
            await asyncio.sleep(BACKOFF * (attempt + 1))
    db.add(result)
    await db.flush()
    await _trim(db, hook.id)
    return result


async def _trim(db: AsyncSession, webhook_id: uuid.UUID) -> None:
    keep = select(WebhookDelivery.id).where(WebhookDelivery.webhook_id == webhook_id)
    keep = keep.order_by(WebhookDelivery.at.desc()).limit(KEEP_DELIVERIES)
    await db.execute(
        delete(WebhookDelivery).where(
            WebhookDelivery.webhook_id == webhook_id, WebhookDelivery.id.not_in(keep)
        )
    )


def startup() -> None:
    busy.register("webhooks-sending", busy_sending)
