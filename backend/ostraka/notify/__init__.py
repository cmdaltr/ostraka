"""Notifications: who hears about what, and how it reaches them.

`send()` writes a notification row per person within the caller's transaction; `team_channel()`
queues a post to a team's Teams channel. Nothing leaves ostraka until `dispatch()` runs after the
commit (`activity.finish` calls it), so a rolled-back change never notifies anyone.

What leaves ostraka is cut down first. Indicators are masked in every email, push, Teams card and webhook,
and for TLP:RED and TLP:AMBER+STRICT incidents only the reference and the kind of event go out:
the title and text stay inside ostraka.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import busy, live, profile
from ostraka.auth.secrets import decrypt
from ostraka.config import get_settings
from ostraka.db import sessions
from ostraka.incidents import access
from ostraka.incidents.masking import mask_text
from ostraka.log import get_logger
from ostraka.models import (
    Incident,
    Notification,
    NotificationKind,
    PushSubscription,
    Task,
    Team,
    Tlp,
    User,
    utcnow,
)
from ostraka.notify import channels

log = get_logger(__name__)

_PENDING = "ostraka.pending_notifications"
_PENDING_TEAMS = "ostraka.pending_team_posts"
_background: set[asyncio.Task] = set()

# Worth an email; everything else is in-app and push only
EMAIL_KINDS = frozenset(
    {
        NotificationKind.ASSIGNED,
        NotificationKind.OVERDUE,
        NotificationKind.ESCALATED,
        NotificationKind.NUDGE,
        NotificationKind.MENTION,
        NotificationKind.COMMANDER,
        NotificationKind.NEW_DEVICE,
        NotificationKind.CLOCK_DUE,
        NotificationKind.CLOCK_BREACHED,
    }
)
URGENT_KINDS = frozenset(
    {
        NotificationKind.OVERDUE,
        NotificationKind.ESCALATED,
        NotificationKind.NUDGE,
        NotificationKind.NEW_DEVICE,
        NotificationKind.CLOCK_BREACHED,
    }
)

KIND_WORDS = {
    NotificationKind.ASSIGNED: "A task was assigned to you",
    NotificationKind.TEAM_TASK: "A task is waiting in your team's queue",
    NotificationKind.DUE_SOON: "A task is due soon",
    NotificationKind.OVERDUE: "A task is overdue",
    NotificationKind.ESCALATED: "An overdue task was escalated to you",
    NotificationKind.NUDGE: "Someone nudged you",
    NotificationKind.MENTION: "You were mentioned",
    NotificationKind.REPLY: "Someone replied to your comment",
    NotificationKind.COMMANDER: "You're now incident commander",
    NotificationKind.UNBLOCKED: "A task you hold is no longer waiting",
    NotificationKind.NEW_DEVICE: "Your account signed in from a new browser",
    NotificationKind.CLOCK_DUE: "A regulator deadline is approaching",
    NotificationKind.CLOCK_BREACHED: "A regulator deadline has passed",
}


def restricted(incident: Incident | None) -> bool:
    return incident is not None and incident.tlp in (Tlp.RED, Tlp.AMBER_STRICT)


def outward(kind: NotificationKind, title: str, body: str, incident: Incident | None) -> tuple[str, str]:
    """The title and text that may leave ostraka for this event."""
    if restricted(incident):
        return (
            f"{KIND_WORDS[kind]} in {incident.ref}",
            f"{incident.ref} is TLP:{incident.tlp.value.upper()}: open Ostraka to see the details.",
        )
    return mask_text(title), mask_text(body)


def outward_data(incident: Incident, data: dict[str, Any]) -> dict[str, Any]:
    """The structured equivalent of `outward`, for webhooks: every string masked, and for a
    restricted incident nothing but its reference (the event's name travels beside it)."""
    if restricted(incident):
        return {"ref": incident.ref}
    return _masked(data)


def _masked(value: Any) -> Any:
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: _masked(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_masked(v) for v in value]
    return value


def link_url(path: str) -> str:
    return get_settings().public_url.rstrip("/") + (path or "/")


def incident_link(incident: Incident, section: str = "", record: str = "") -> str:
    query = f"?s={section}" if section else ""
    return f"/incidents/{incident.ref}{query}{'#' + record if record else ''}"


def send(
    db: AsyncSession,
    recipients: Iterable[User],
    kind: NotificationKind,
    title: str,
    *,
    body: str = "",
    link: str = "",
    incident: Incident | None = None,
    task: Task | None = None,
    actor: User | None = None,
) -> list[Notification]:
    """Notify each recipient once. The actor isn't told about their own action, and nobody hears
    about an incident they can't see."""
    seen: set[uuid.UUID] = set()
    made = []
    for user in recipients:
        if user is None or user.id in seen or not user.is_active or (actor and user.id == actor.id):
            continue
        if incident is not None and not access.can_view(user, incident):
            continue
        seen.add(user.id)
        notification = Notification(
            id=uuid.uuid4(),
            user_id=user.id,
            kind=kind,
            incident_id=incident.id if incident else None,
            task_id=task.id if task else None,
            actor_id=actor.id if actor else None,
            title=title[:300],
            body=body,
            link=link,
        )
        db.add(notification)
        made.append(notification)
    db.info.setdefault(_PENDING, []).extend(n.id for n in made)
    return made


@dataclass(frozen=True)
class _TeamPost:
    team_id: uuid.UUID
    kind: NotificationKind
    title: str
    body: str
    link: str
    incident_id: uuid.UUID | None


def team_channel(
    db: AsyncSession,
    team: Team,
    kind: NotificationKind,
    title: str,
    *,
    body: str = "",
    link: str = "",
    incident: Incident | None = None,
) -> bool:
    """Queue a post to the team's Teams channel. False when the team hasn't connected one."""
    if not team.teams_webhook:
        return False
    db.info.setdefault(_PENDING_TEAMS, []).append(
        _TeamPost(team.id, kind, title, body, link, incident.id if incident else None)
    )
    return True


async def dispatch(db: AsyncSession) -> None:
    """After a commit: tell each recipient's open tabs, then email and push in the background."""
    ids: list[uuid.UUID] = db.info.pop(_PENDING, [])
    posts: list[_TeamPost] = db.info.pop(_PENDING_TEAMS, [])
    if not ids and not posts:
        return
    users = (
        set((await db.scalars(select(Notification.user_id).where(Notification.id.in_(ids)))).all())
        if ids
        else set()
    )
    for user_id in users:
        await live.publish_user(user_id, {"type": "notification"})
    job = asyncio.create_task(_deliver(ids, posts))
    _background.add(job)
    job.add_done_callback(_background.discard)


async def wait() -> None:
    """Let in-flight deliveries finish (shutdown and tests)."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


def busy_sending() -> bool:
    return bool(_background)


async def _deliver(ids: list[uuid.UUID], posts: list[_TeamPost]) -> None:
    try:
        async with sessions()() as db:
            for notification_id in ids:
                notification = await db.get(Notification, notification_id)
                if notification is not None:
                    await _deliver_one(db, notification)
            for post in posts:
                team = await db.get(Team, post.team_id)
                incident = await db.get(Incident, post.incident_id) if post.incident_id else None
                if team and team.teams_webhook:
                    title, text = outward(post.kind, post.title, post.body, incident)
                    await channels.send_teams(decrypt(team.teams_webhook), title, text, link_url(post.link))
            await db.commit()
    except Exception:
        log.exception("notification delivery failed")


async def _deliver_one(db: AsyncSession, notification: Notification) -> None:
    user = await db.get(User, notification.user_id)
    if user is None or not user.is_active:
        return
    incident = await db.get(Incident, notification.incident_id) if notification.incident_id else None
    title, text = outward(notification.kind, notification.title, notification.body, incident)
    url = link_url(notification.link)

    push, email = profile.outside_channels(user, notification.kind, incident, utcnow())
    if push:
        subscriptions = (
            await db.scalars(select(PushSubscription).where(PushSubscription.user_id == user.id))
        ).all()
        payload = {"title": title, "body": text, "link": notification.link, "tag": str(notification.id)}
        for sub in subscriptions:
            status = await channels.send_push(
                sub.endpoint, sub.p256dh, sub.auth, payload, urgent=notification.kind in URGENT_KINDS
            )
            if status in (404, 410):  # the browser unsubscribed or the subscription expired
                await db.execute(delete(PushSubscription).where(PushSubscription.id == sub.id))
            elif 200 <= status < 300:
                sub.last_used_at = utcnow()

    if email and notification.kind in EMAIL_KINDS:
        await channels.send_email(user.email, f"[Ostraka] {title}", f"{text}\n\n{url}\n".lstrip())


def startup() -> None:
    busy.register("notifications-sending", busy_sending)
