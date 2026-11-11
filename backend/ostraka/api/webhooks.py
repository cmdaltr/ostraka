"""The installation's webhooks (`ostraka.webhooks`): admin only, and every change in the security log.

Registering and listing work on every edition. Only sending needs `automation`, so a Free
installation keeps its webhooks and the page says why they're quiet.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit, licence, webhooks
from ostraka.auth import policy, sessions
from ostraka.auth.deps import current_user
from ostraka.auth.secrets import encrypt, new_token
from ostraka.db import get_session
from ostraka.incidents.views import check_version
from ostraka.installation_schemas import (
    DeliveryOut,
    WebhookIn,
    WebhookOut,
    WebhookPatch,
    WebhooksOut,
)
from ostraka.models import User, Webhook, WebhookDelivery

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

NOT_SENDING = "Webhooks are part of the Pro and Services editions. Install a licence to send them."


def _admin(user: User) -> None:
    if problem := policy.can_manage_webhooks(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, problem)


def _check(url: str, events: list[str]) -> None:
    if problem := webhooks.url_problem(url) or webhooks.events_problem(events):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)


async def _out(db: AsyncSession, hook: Webhook, secret: str | None = None) -> WebhookOut:
    deliveries = (
        await db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.webhook_id == hook.id)
            .order_by(WebhookDelivery.at.desc())
            .limit(webhooks.KEEP_DELIVERIES)
        )
    ).all()
    return WebhookOut(
        id=hook.id,
        url=hook.url,
        events=list(hook.events),
        description=hook.description,
        enabled=hook.enabled,
        has_secret=bool(hook.secret),
        created_at=hook.created_at,
        version=hook.version,
        deliveries=[DeliveryOut.model_validate(d, from_attributes=True) for d in deliveries],
        secret=secret,
    )


async def _hook(db: AsyncSession, hook_id: uuid.UUID) -> Webhook:
    hook = await db.get(Webhook, hook_id)
    if hook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such webhook.")
    return hook


@router.get("", response_model=WebhooksOut)
async def list_webhooks(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    _admin(user)
    hooks = (await db.scalars(select(Webhook).order_by(Webhook.created_at))).all()
    return WebhooksOut(
        events=list(webhooks.EVENTS),
        sending=(await licence.current(db)).has(licence.AUTOMATION),
        webhooks=[await _out(db, h) for h in hooks],
    )


@router.post("", response_model=WebhookOut, status_code=status.HTTP_201_CREATED)
async def add_webhook(
    body: WebhookIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> WebhookOut:
    """The secret is returned here once; afterwards ostraka only says whether there is one."""
    _admin(user)
    _check(body.url, body.events)
    secret = body.secret or new_token(24)
    hook = Webhook(
        id=uuid.uuid4(),
        url=body.url.strip(),
        events=sorted(set(body.events)),
        secret=encrypt(secret),
        description=body.description.strip(),
        enabled=True,
        created_by=user.id,
        version=1,
    )
    db.add(hook)
    audit.record(
        db,
        "webhook_added",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        url=hook.url,
        events=hook.events,
    )
    await db.commit()
    return await _out(db, hook, secret)


@router.patch("/{hook_id}", response_model=WebhookOut)
async def change_webhook(
    hook_id: uuid.UUID,
    body: WebhookPatch,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> WebhookOut:
    _admin(user)
    hook = await _hook(db, hook_id)
    check_version(hook.version, body.version, "webhook")
    changes = body.model_dump(exclude_unset=True, exclude={"version"}, exclude_none=True)
    _check(changes.get("url", hook.url), changes.get("events", hook.events))
    if "url" in changes:
        changes["url"] = changes["url"].strip()
    if "events" in changes:
        changes["events"] = sorted(set(changes["events"]))
    for field, value in changes.items():
        setattr(hook, field, value)
    if changes:
        hook.version += 1
        audit.record(
            db,
            "webhook_changed",
            actor_id=user.id,
            ip=sessions.client_ip(request),
            webhook=str(hook.id),
            changes=sorted(changes),
        )
        await db.commit()
    return await _out(db, hook)


@router.delete("/{hook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_webhook(
    hook_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    _admin(user)
    hook = await _hook(db, hook_id)
    audit.record(db, "webhook_removed", actor_id=user.id, ip=sessions.client_ip(request), url=hook.url)
    await db.delete(hook)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{hook_id}/test", response_model=DeliveryOut)
async def test_webhook(
    hook_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> DeliveryOut:
    """Send a made-up incident.created now, once, and wait for the answer. Sending needs the
    licence, so this does too."""
    _admin(user)
    hook = await _hook(db, hook_id)
    if not (await licence.current(db)).has(licence.AUTOMATION):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, NOT_SENDING)
    data = {"ref": "OST-TEST", "kind": "incident", "title": "Ostraka webhook test", "test": True}
    delivery = await webhooks.deliver(db, hook, "incident.created", data, tries=1)
    await db.commit()
    return DeliveryOut.model_validate(delivery, from_attributes=True)
