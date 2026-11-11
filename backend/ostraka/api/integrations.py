"""The installation's Rowanholt and Shikari servers (`ostraka.integrations`), and Shikari's webhook.

Connecting a server is admin only, and every change is in the security log. Tokens and the webhook
secret are stored encrypted and never shown again; Shikari's webhook secret is shown once, when it's
made, for the admin to give to Shikari.

`POST /api/integrations/shikari/webhook` takes no sign-in: Shikari signs each delivery with that
secret (`X-Shikari-Signature`), and anything unsigned or wrongly signed is refused and logged. On
`run.completed` Ostraka asks Shikari's coverage API about every open detection gap. Acting on a
webhook is part of the `automation` feature, as sending them is, so a Free installation
acknowledges them and does nothing; asking from an incident page works on every edition.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit, licence, notify
from ostraka.auth import policy, sessions
from ostraka.auth.deps import current_user
from ostraka.auth.secrets import decrypt, encrypt, new_token
from ostraka.db import get_session
from ostraka.incidents import views
from ostraka.integration_schemas import (
    CheckStep,
    ConnectionTest,
    IntegrationIn,
    IntegrationOut,
    IntegrationsOut,
)
from ostraka.integrations import NAMES, gaps, rowanholt, shikari, signing, url_problem
from ostraka.integrations.http import Refused, Unreachable
from ostraka.models import Integration, Product, Team, User, utcnow

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

WEBHOOK_PATH = "/api/integrations/shikari/webhook"
MAX_WEBHOOK_BODY = 256 * 1024


def _admin(user: User) -> None:
    if problem := policy.can_manage_integrations(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, problem)


async def _out(db: AsyncSession, row: Integration | None, secret: str | None = None) -> IntegrationOut | None:
    if row is None:
        return None
    lookup = await views.names(db, [row.updated_by])
    return IntegrationOut(
        product=row.product,
        url=row.url,
        has_token=bool(row.token),
        has_webhook_secret=bool(row.webhook_secret),
        detection_team_id=row.detection_team_id,
        enabled=row.enabled,
        checked_at=row.checked_at,
        check_ok=row.check_ok,
        check_message=row.check_message,
        updated_by=views.person(lookup, row.updated_by),
        updated_at=row.updated_at,
        version=row.version,
        webhook_secret=secret,
    )


@router.get("", response_model=IntegrationsOut)
async def list_integrations(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    _admin(user)
    return IntegrationsOut(
        rowanholt=await _out(db, await db.get(Integration, Product.ROWANHOLT)),
        shikari=await _out(db, await db.get(Integration, Product.SHIKARI)),
        shikari_webhook_url=notify.link_url(WEBHOOK_PATH),
        automation=(await licence.current(db)).has(licence.AUTOMATION),
    )


@router.put("/{product}", response_model=IntegrationOut)
async def save_integration(
    product: Product,
    body: IntegrationIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IntegrationOut:
    """Connect a server, or change it. A token left out keeps the one stored; an empty one removes it
    (Rowanholt in local mode needs none)."""
    _admin(user)
    if problem := url_problem(body.url):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    if body.detection_team_id and await db.get(Team, body.detection_team_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such team.")
    row = await db.get(Integration, product)
    secret = None
    if row is None:
        row = Integration(product=product, version=1, token="", webhook_secret="", check_message="")
        if product is Product.SHIKARI:
            secret = new_token(24)
            row.webhook_secret = encrypt(secret)
        db.add(row)
        action = "integration_added"
    else:
        if body.version is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Send the version you loaded.")
        views.check_version(row.version, body.version, "server")
        row.version += 1
        action = "integration_changed"
    if body.url.strip().rstrip("/") != row.url:
        row.checked_at, row.check_ok, row.check_message = None, None, ""
    row.url = body.url.strip().rstrip("/")
    if body.token is not None:
        row.token = encrypt(body.token.strip()) if body.token.strip() else ""
    row.detection_team_id = body.detection_team_id if product is Product.SHIKARI else None
    row.enabled = body.enabled
    row.updated_by, row.updated_at = user.id, utcnow()
    audit.record(
        db,
        action,
        actor_id=user.id,
        ip=sessions.client_ip(request),
        product=product.value,
        url=row.url,
        token_changed=body.token is not None,
        enabled=row.enabled,
    )
    await db.commit()
    return await _out(db, row, secret)


@router.post("/shikari/secret", response_model=IntegrationOut)
async def replace_webhook_secret(
    request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> IntegrationOut:
    """A new secret for Shikari's webhook, shown once. The old one stops working at once."""
    _admin(user)
    row = await db.get(Integration, Product.SHIKARI)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connect Shikari first.")
    secret = new_token(24)
    row.webhook_secret = encrypt(secret)
    row.version += 1
    audit.record(
        db, "integration_secret_replaced", actor_id=user.id, ip=sessions.client_ip(request), product="shikari"
    )
    await db.commit()
    return await _out(db, row, secret)


@router.delete("/{product}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_integration(
    product: Product,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Disconnect a server. Links, citations and gaps already recorded stay in their incidents."""
    _admin(user)
    row = await db.get(Integration, product)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{NAMES[product]} isn't connected.")
    audit.record(
        db,
        "integration_removed",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        product=product.value,
        url=row.url,
    )
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _step(steps: list[CheckStep], name: str, call) -> object | None:
    try:
        answer = await call
    except (Unreachable, Refused) as exc:
        steps.append(CheckStep(name=name, ok=False, detail=str(exc)))
        return None
    steps.append(CheckStep(name=name, ok=True, detail=""))
    return answer


async def _test_rowanholt(row: Integration) -> list[CheckStep]:
    client = rowanholt.Rowanholt.of(row)
    steps: list[CheckStep] = []
    health = await _step(steps, "Rowanholt answers", client.health())
    if health is None:
        return steps
    if isinstance(health, dict) and health.get("status") != "healthy":
        steps[-1] = CheckStep(name="Rowanholt answers", ok=False, detail="That doesn't look like Rowanholt.")
        return steps
    me = await _step(steps, "The token signs in", client.me())
    if isinstance(me, dict):
        scope = (me.get("token") or {}).get("scope", "")
        who = me.get("username") or me.get("name") or "someone"
        if scope in ("aule-mcp", "eru-mcp"):
            steps[-1].detail = f"As {who}, read-only."
        elif scope:
            steps[
                -1
            ].detail = (
                f"As {who}, with a {scope} token. Ostraka only reads: a read-only aule-mcp token is enough."
            )
        else:
            steps[-1].detail = f"As {who} (no token: Rowanholt in local mode)."
        jobs = await _step(steps, "Cases can be read", client._get("/api/jobs", limit=1, log_limit=0))
        if isinstance(jobs, dict):
            steps[-1].detail = f"{jobs.get('total', 0)} jobs visible to this token."
    return steps


async def _test_shikari(row: Integration) -> list[CheckStep]:
    client = shikari.Shikari.of(row)
    steps: list[CheckStep] = []
    health = await _step(steps, "Shikari answers", client.health())
    if health is None:
        return steps
    if isinstance(health, dict) and health.get("app") != "shikari":
        steps[-1] = CheckStep(name="Shikari answers", ok=False, detail="That doesn't look like Shikari.")
        return steps
    coverage = await _step(steps, "The token reads coverage", client.coverage(["T1059"]))
    if coverage is not None:
        steps[-1].detail = "Coverage for T1059 read."
    return steps


@router.post("/{product}/test", response_model=ConnectionTest)
async def test_integration(
    product: Product, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> ConnectionTest:
    """Reach the saved server with the saved token, step by step, and keep the result."""
    _admin(user)
    row = await db.get(Integration, product)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{NAMES[product]} isn't connected.")
    steps = await (_test_rowanholt(row) if product is Product.ROWANHOLT else _test_shikari(row))
    ok = bool(steps) and all(s.ok for s in steps)
    failed = next((s for s in steps if not s.ok), None)
    message = "Connected." if ok else (failed.detail if failed else "Not tested.")
    row.checked_at, row.check_ok, row.check_message = utcnow(), ok, message[:300]
    await db.commit()
    return ConnectionTest(ok=ok, message=message, steps=steps)


@router.post("/shikari/webhook")
async def shikari_webhook(request: Request, db: AsyncSession = Depends(get_session)) -> JSONResponse:
    """Shikari's signed webhook. Answers 202 once the signature checks out, whatever is done with it."""
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BODY:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Too large.")
    row = await db.get(Integration, Product.SHIKARI)
    secret = decrypt(row.webhook_secret) if row is not None and row.webhook_secret else ""
    if not shikari.signature_ok(secret, body, request.headers.get(shikari.SIGNATURE)):
        audit.record(
            db,
            "shikari_webhook_refused",
            ip=sessions.client_ip(request),
            event=(request.headers.get(shikari.EVENT) or "")[:40],
            configured=row is not None,
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad or missing signature.")
    try:
        delivery = signing.parse(body)
    except signing.NotADelivery:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Not a Shikari webhook.") from None
    event, data = delivery.event, delivery.data
    if event not in shikari.RECHECK_EVENTS:
        return JSONResponse(
            {"accepted": False, "reason": "Ostraka doesn't act on this event."}, status_code=202
        )
    if not row.enabled:
        return JSONResponse(
            {"accepted": False, "reason": "Shikari is switched off in Ostraka."}, status_code=202
        )
    if not (await licence.current(db)).has(licence.AUTOMATION):
        return JSONResponse(
            {
                "accepted": False,
                "reason": "Acting on webhooks is part of Ostraka's Pro and Services editions.",
            },
            status_code=202,
        )
    run = str(data.get("run") or "")[:120]
    gaps.queue_recheck(f"Shikari run {run} completed" if run else "A Shikari run completed")
    return JSONResponse({"accepted": True}, status_code=202)
