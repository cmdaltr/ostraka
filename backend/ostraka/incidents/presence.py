"""Presence on objects, and warnings when an indicator is being worked in another incident.

A viewer's focus names what they have open, by id only: a section ("findings") or an object
("finding:<uuid>"). Anything else a browser sends is dropped, because presence is broadcast to every
viewer of the incident, observers included, and must never carry content.

"Working elsewhere" is someone else, in another open incident, who has the same observable open now
or has changed it within `ACTIVE_WINDOW`. Incidents the viewer can see are named with the people
working them; others are only counted, with the teams to ask, as the overlap already is, and never
for TLP:RED or TLP:AMBER+STRICT (`policy.may_signal_work_elsewhere`).
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import live
from ostraka.auth import policy
from ostraka.incidents import access
from ostraka.models import (
    ActivityEvent,
    Incident,
    IncidentObservable,
    IncidentStatus,
    Role,
    Team,
    User,
    utcnow,
)

# Objects whose presence is shown; a focus on anything else is reduced to its section
OBJECT_KINDS = frozenset(
    {"finding", "task", "observable", "asset", "comment", "assessment", "hypothesis", "evidence", "lesson"}
)
_SECTION = re.compile(r"^[a-z][a-z-]{0,23}$")
_OBJECT = re.compile(r"^([a-z]+):([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")

ACTIVE_WINDOW = timedelta(hours=2)


def clean_focus(raw: object) -> str:
    """The focus a browser sent, if it's a section name or a known object kind with a UUID; else ""."""
    text = str(raw or "").strip().lower()
    if _SECTION.match(text):
        return text
    match = _OBJECT.match(text)
    if match and match[1] in OBJECT_KINDS:
        return text
    return ""


@dataclass
class ElsewhereWork:
    incident_id: uuid.UUID
    ref: str
    title: str
    people: set[str] = field(default_factory=set)
    editing: bool = False
    last_at: datetime | None = None


@dataclass
class Working:
    """For one observable in this incident: who else is on it, in which other incidents."""

    item_id: uuid.UUID
    visible: list[ElsewhereWork] = field(default_factory=list)
    hidden: int = 0
    ask: list[str] = field(default_factory=list)


async def working_elsewhere(db: AsyncSession, viewer: User, incident: Incident) -> list[Working]:
    """Observables in `incident` that someone other than `viewer` is working in another open incident."""
    mine = dict(
        (
            await db.execute(
                select(IncidentObservable.observable_id, IncidentObservable.id).where(
                    IncidentObservable.incident_id == incident.id
                )
            )
        ).all()
    )
    if not mine:
        return []
    others = (
        await db.execute(
            select(IncidentObservable.id, IncidentObservable.observable_id, Incident)
            .join(Incident, Incident.id == IncidentObservable.incident_id)
            .where(
                IncidentObservable.observable_id.in_(mine.keys()),
                Incident.id != incident.id,
                Incident.status == IncidentStatus.OPEN,
            )
        )
    ).all()
    if not others:
        return []

    since = utcnow() - ACTIVE_WINDOW
    recent: dict[uuid.UUID, list[tuple[uuid.UUID, datetime]]] = defaultdict(list)
    for entity_id, actor_id, at in (
        await db.execute(
            select(ActivityEvent.entity_id, ActivityEvent.actor_id, func.max(ActivityEvent.at))
            .where(
                ActivityEvent.entity_type == "observable",
                ActivityEvent.entity_id.in_([row[0] for row in others]),
                ActivityEvent.at >= since,
                ActivityEvent.actor_id.is_not(None),
                ActivityEvent.actor_id != viewer.id,
            )
            .group_by(ActivityEvent.entity_id, ActivityEvent.actor_id)
        )
    ).all():
        recent[entity_id].append((actor_id, at))
    actor_names = (
        dict(
            (
                await db.execute(
                    select(User.id, User.name).where(
                        User.id.in_({a for rows in recent.values() for a, _ in rows}),
                        User.role != Role.OBSERVER,
                    )
                )
            ).all()
        )
        if recent
        else {}
    )

    found: dict[uuid.UUID, Working] = {}
    hidden_teams: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for other_item_id, observable_id, other in others:
        present = [
            v
            for v in live.hub.focused(str(other.id), f"observable:{other_item_id}")
            if v.user_id != str(viewer.id)
        ]
        changed = [(actor_names[a], at) for a, at in recent.get(other_item_id, []) if a in actor_names]
        if not present and not changed:
            continue
        can_view = access.can_view(viewer, other)
        if not policy.may_signal_work_elsewhere(can_view=can_view, tlp=other.tlp):
            continue
        entry = found.setdefault(mine[observable_id], Working(item_id=mine[observable_id]))
        if can_view:
            work = ElsewhereWork(other.id, other.ref, other.title)
            work.people.update(v.name for v in present)
            work.people.update(name for name, _ in changed)
            work.editing = any(v.editing for v in present)
            work.last_at = max((at for _, at in changed), default=None)
            entry.visible.append(work)
        else:
            entry.hidden += 1
            hidden_teams[entry.item_id].update(t.team_id for t in other.teams)

    team_ids = set().union(*hidden_teams.values()) if hidden_teams else set()
    if team_ids:
        teams = dict((await db.execute(select(Team.id, Team.name).where(Team.id.in_(team_ids)))).all())
        for item_id, ids in hidden_teams.items():
            found[item_id].ask = sorted(teams[t] for t in ids if t in teams)
    return list(found.values())
