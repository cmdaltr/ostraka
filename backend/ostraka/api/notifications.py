"""Your notifications, and the browsers you've asked to receive them by push."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incidents import views
from ostraka.incidents.masking import for_viewer
from ostraka.models import Notification, PushSubscription, User, utcnow
from ostraka.notify import push
from ostraka.work_schemas import NotificationOut, NotificationsOut, PushEndpoint, PushSubscriptionIn

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=NotificationsOut)
async def list_notifications(
    limit: int = Query(default=30, ge=1, le=200),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> NotificationsOut:
    """The newest first, with how many are unread overall."""
    items = (
        await db.scalars(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
    ).all()
    unread = await db.scalar(
        select(func.count()).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    lookup = await views.names(db, [n.actor_id for n in items])
    return NotificationsOut(
        items=[
            NotificationOut(
                id=n.id,
                kind=n.kind,
                title=for_viewer(user, n.title),
                body=for_viewer(user, n.body),
                link=n.link,
                actor=views.person(lookup, n.actor_id),
                created_at=n.created_at,
                read_at=n.read_at,
            )
            for n in items
        ],
        unread=unread or 0,
    )


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def mark_read(
    notification_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> None:
    result = await db.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
    if result.rowcount == 0 and not await db.scalar(
        select(Notification.id).where(Notification.id == notification_id, Notification.user_id == user.id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such notification.")
    await db.commit()


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def mark_all_read(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)) -> None:
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    await db.commit()


@router.get("/push-key")
async def push_key(_: User = Depends(current_user)) -> dict:
    """The application server key browsers subscribe with."""
    return {"key": push.vapid_public_key()}


@router.post("/push-subscriptions", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def subscribe(
    body: PushSubscriptionIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Remember this browser for push. A browser that was someone else's moves to you."""
    values = {
        "user_id": user.id,
        "p256dh": body.keys.p256dh,
        "auth": body.keys.auth,
        "user_agent": body.user_agent,
    }
    await db.execute(
        insert(PushSubscription)
        .values(id=uuid.uuid4(), endpoint=body.endpoint, **values)
        .on_conflict_do_update(index_elements=["endpoint"], set_=values)
    )
    audit.record(db, "push_subscribed", actor_id=user.id)
    await db.commit()


@router.delete("/push-subscriptions", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def unsubscribe(
    body: PushEndpoint, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> None:
    await db.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == user.id
        )
    )
    await db.commit()
