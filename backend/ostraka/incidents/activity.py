"""The activity log: an append-only, hash-chained record of everything done in an incident.

Every change goes through `record()`, which takes a row lock on the incident so positions and
hashes are assigned one at a time. `finish()` commits, then publishes the new events to anyone
watching the incident live.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import live, notify, webhooks
from ostraka.models import ActivityEvent, Incident, User, utcnow

GENESIS = "0" * 64
_PENDING = "ostraka.pending_events"


def _digest(event: ActivityEvent) -> str:
    body = {
        "incident_id": str(event.incident_id),
        "position": event.position,
        "at": event.at.isoformat(),
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": str(event.entity_id) if event.entity_id else None,
        "summary": event.summary,
        "data": event.data,
        "prev_hash": event.prev_hash,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def record(
    db: AsyncSession,
    incident: Incident,
    actor: User | None,
    action: str,
    summary: str,
    *,
    entity_type: str = "incident",
    entity_id: uuid.UUID | None = None,
    **data: Any,
) -> ActivityEvent:
    """Append an event to the incident's chain. Written with the caller's commit."""
    await db.execute(select(Incident.id).where(Incident.id == incident.id).with_for_update())
    last = await db.scalar(
        select(ActivityEvent)
        .where(ActivityEvent.incident_id == incident.id)
        .order_by(ActivityEvent.position.desc())
        .limit(1)
    )
    # Events added earlier in this same transaction aren't flushed yet; count them too
    pending: list[ActivityEvent] = db.info.setdefault(_PENDING, [])
    mine = [e for e in pending if e.incident_id == incident.id]
    previous = mine[-1] if mine else last
    event = ActivityEvent(
        id=uuid.uuid4(),
        incident_id=incident.id,
        position=(previous.position + 1) if previous else 1,
        at=utcnow(),
        actor_id=actor.id if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        data={k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in data.items()},
        prev_hash=previous.hash if previous else GENESIS,
    )
    event.hash = _digest(event)
    db.add(event)
    pending.append(event)
    return event


def event_json(event: ActivityEvent, actor_name: str | None = None) -> dict[str, Any]:
    """What a live viewer is told: that something changed and where, never the wording or data.

    Summaries can carry raw indicators, and one broadcast reaches every viewer, observers included, so
    clients refetch through the API, which masks per viewer.
    """
    return {
        "incident_id": str(event.incident_id),
        "position": event.position,
        "at": event.at.isoformat(),
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "actor_name": actor_name,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": str(event.entity_id) if event.entity_id else None,
    }


async def finish(db: AsyncSession, actor: User | None = None) -> None:
    """Commit, then tell live viewers what changed and send any notifications and webhooks."""
    pending: list[ActivityEvent] = db.info.pop(_PENDING, [])
    await db.commit()
    for event in pending:
        await live.publish_incident(
            event.incident_id, {"type": "activity", "event": event_json(event, actor.name if actor else None)}
        )
    await notify.dispatch(db)
    webhooks.queue(pending)


@dataclass
class ChainCheck:
    ok: bool
    events: int
    broken_at: int | None = None


async def verify(db: AsyncSession, incident_id: uuid.UUID) -> ChainCheck:
    """Recompute every hash in order. The first position whose hash or link doesn't match is reported."""
    events = (
        await db.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.incident_id == incident_id)
            .order_by(ActivityEvent.position)
        )
    ).all()
    previous = GENESIS
    for expected, event in enumerate(events, start=1):
        if event.position != expected or event.prev_hash != previous or _digest(event) != event.hash:
            return ChainCheck(False, len(events), event.position)
        previous = event.hash
    return ChainCheck(True, len(events))


async def count(db: AsyncSession, incident_id: uuid.UUID) -> int:
    return await db.scalar(select(func.count()).where(ActivityEvent.incident_id == incident_id)) or 0
