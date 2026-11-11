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
The body, the signature, the URL rules and the tries are samarkand's (`samarkand.webhooks`), shared
with Shikari and Rowanholt; which events exist, what they say, and where results are kept are ours.

`data` goes through `notify.outward_data` like every other message that leaves ostraka:
indicators masked, and a TLP:RED or TLP:AMBER+STRICT record named by its reference only.

The licence is checked when a webhook is sent, never when it's saved: a Free installation keeps
its webhooks, and they start firing again when a licence with `automation` is installed.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from samarkand import webhooks as shared
from samarkand.webhooks import sign, url_problem  # noqa: F401  (re-exported for the API and tests)
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
PREFIX = "X-Ostraka"
USER_AGENT = "ostraka-webhook/1"  # samarkand's default would be Ostraka-Webhook/1
KEEP_DELIVERIES = shared.KEEP_DELIVERIES
TRIES = shared.TRIES
BACKOFF = shared.BACKOFF  # seconds, times the attempt number
TIMEOUT = 15.0  # as every other message Ostraka sends (notify.channels.fetch)
_TASK_ACTIONS = frozenset({"task_added", "task_updated", "task_claimed"})
_ACTIONS = frozenset({"declared", "incident_updated"}) | _TASK_ACTIONS
_background: set[asyncio.Task] = set()


def events_problem(events: list[str]) -> str | None:
    """Why a webhook can't subscribe to `events`, or None."""
    return shared.events_problem(events, EVENTS)


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
    """The bytes sent and signed."""
    return shared.body(event, data, sent_at or utcnow())


async def _post(url: str, raw: bytes, headers: Mapping[str, str], _timeout: float) -> int:
    # Through notify.channels, Ostraka's one way out, which tests replace; its fetch has the same
    # TIMEOUT and doesn't follow redirects
    return await channels.post(url, content=raw, headers=dict(headers))


async def deliver(
    db: AsyncSession, hook: Webhook, event: str, data: dict[str, Any], *, tries: int = TRIES
) -> WebhookDelivery:
    """POST one event to one webhook and keep the result. The caller commits. A URL that the rules
    no longer allow (one saved before they tightened) isn't sent, and the result says why."""
    at = utcnow()
    outgoing = shared.prepare(
        hook.url,
        event,
        data,
        prefix=PREFIX,
        secret=decrypt(hook.secret) if hook.secret else None,
        user_agent=USER_AGENT,
        sent_at=at,
    )
    sent = await shared.send_async(outgoing, tries=tries, backoff=BACKOFF, timeout=TIMEOUT, post=_post)
    result = WebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=hook.id,
        event=event,
        at=at,
        ok=sent.ok,
        status=sent.status,
        error=sent.error[:200],
        tries=sent.tries,
    )
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
