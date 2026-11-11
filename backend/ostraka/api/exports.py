"""Intelligence exports: STIX 2.1, MISP and an ATT&CK Navigator layer (`ostraka.exports`), and
pushing an incident to the installation's MISP.

Downloading is for the people who may export the incident's reports, on every edition: it's the
core product, a file handed to the person who asked. TLP:RED and TLP:AMBER+STRICT incidents download
for their commander only (`policy.can_export_intel`). Pushing calls another system, so it's the
`automation` feature, like webhooks; it happens only when someone asks, never on its own, and never
for TLP:RED or TLP:AMBER+STRICT. The MISP settings are admin only and kept on every edition.

Every export and push is written to the security log and to the incident's activity chain.
"""

from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit, licence, notify, webhooks
from ostraka.auth import policy, sessions
from ostraka.auth.deps import current_user
from ostraka.auth.secrets import decrypt, encrypt
from ostraka.db import get_session
from ostraka.exports import misp, navigator, record, stix
from ostraka.incidents import access, activity
from ostraka.incidents.views import check_version
from ostraka.models import Incident, MispSettings, User, utcnow

router = APIRouter(tags=["exports"])

NOT_PUSHING = "Pushing to MISP is part of the Pro and Services editions. Downloads work on every edition."
PUSH_WORDS = {"added": "sent the incident to", "updated": "updated the incident in"}
FORMAT_WORDS = {"stix": "a STIX 2.1 bundle", "misp": "a MISP event", "navigator": "an ATT&CK Navigator layer"}


class PushState(BaseModel):
    available: bool
    reason: str | None = None


class ExportsOut(BaseModel):
    """What the incident's Export menu offers this person."""

    downloads: bool
    reason: str | None = None
    misp: PushState


class PushedOut(BaseModel):
    action: str
    event_id: str
    uuid: str
    url: str


class MispOut(BaseModel):
    configured: bool
    url: str = ""
    verify_tls: bool = True
    distribution: int = 0
    has_key: bool = False
    sending: bool  # the edition includes automation
    distributions: dict[int, str]
    version: int = 0


class MispIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    api_key: str | None = Field(default=None, max_length=200, description="Leave out to keep the current key")
    verify_tls: bool = True
    distribution: int = Field(default=0, ge=0, le=3)
    version: int | None = None


class MispCheckOut(BaseModel):
    ok: bool
    version: str = ""
    error: str = ""


async def _intel_refusal(
    db: AsyncSession, user: User, incident: Incident, *, push: bool = False
) -> str | None:
    return policy.can_export_intel(
        user,
        worked_on=await access.worked_on(db, user, incident),
        is_commander=access.commander_id(incident) == user.id,
        sensitive=incident.sensitive,
        restricted=notify.restricted(incident),
        push=push,
    )


async def _settings(db: AsyncSession) -> MispSettings | None:
    return await db.get(MispSettings, 1)


@router.get("/api/incidents/{incident_id}/exports", response_model=ExportsOut)
async def exports(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> ExportsOut:
    incident = await access.load(db, user, incident_id)
    why = await _intel_refusal(db, user, incident)
    push_why = await _intel_refusal(db, user, incident, push=True)
    if push_why is None and await _settings(db) is None:
        push_why = "No MISP instance is set up. An admin can add one under Installation."
    if push_why is None and not (await licence.current(db)).has(licence.AUTOMATION):
        push_why = NOT_PUSHING
    return ExportsOut(
        downloads=why is None, reason=why, misp=PushState(available=push_why is None, reason=push_why)
    )


@router.get("/api/incidents/{incident_id}/export/{fmt}", response_class=Response)
async def export_intel(
    incident_id: uuid.UUID,
    fmt: Literal["stix", "misp", "navigator"],
    request: Request,
    leads: bool = Query(default=False, description="Navigator: add techniques named only by leads"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    incident = await access.load(db, user, incident_id)
    if why := await _intel_refusal(db, user, incident):
        raise HTTPException(status.HTTP_403_FORBIDDEN, why)
    intel = await record.gather(db, incident)
    now = utcnow()
    if fmt == "stix":
        body, media = stix.bundle(intel, now), stix.MEDIA_TYPE
    elif fmt == "misp":
        settings = await _settings(db)
        body = misp.event(intel, distribution=settings.distribution if settings else 0, now=now)
        media = "application/json"
    else:
        body, media = navigator.layer(intel, leads=leads), "application/json"

    detail = {"leads": leads} if fmt == "navigator" else {}
    audit.record(
        db,
        "intel_exported",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        incident=incident.ref,
        format=fmt,
        tlp=incident.tlp.value,
        **detail,
    )
    await activity.record(
        db,
        incident,
        user,
        "intel_exported",
        f"{user.name} exported {FORMAT_WORDS[fmt]}" + (" with leads" if leads and fmt == "navigator" else ""),
        format=fmt,
        tlp=incident.tlp.value,
        **detail,
    )
    await activity.finish(db, user)
    name = f"{incident.ref}-{fmt}.json"
    return Response(
        json.dumps(body, indent=2, ensure_ascii=False).encode(),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


@router.post("/api/incidents/{incident_id}/misp", response_model=PushedOut)
async def push_to_misp(
    incident_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> PushedOut:
    """Send the incident to the installation's MISP now: added the first time, updated after."""
    incident = await access.load(db, user, incident_id)
    if why := await _intel_refusal(db, user, incident, push=True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, why)
    settings = await _settings(db)
    if settings is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No MISP instance is set up.")
    if not (await licence.current(db)).has(licence.AUTOMATION):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, NOT_PUSHING)
    payload = misp.event(await record.gather(db, incident), distribution=settings.distribution, now=utcnow())
    try:
        pushed = await misp.push(settings.url, decrypt(settings.api_key), settings.verify_tls, payload)
    except misp.MispProblem as exc:
        audit.record(
            db,
            "misp_push_failed",
            actor_id=user.id,
            ip=sessions.client_ip(request),
            incident=incident.ref,
            error=str(exc)[:200],
        )
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    audit.record(
        db,
        "misp_pushed",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        incident=incident.ref,
        outcome=pushed.action,
        event=pushed.uuid,
        tlp=incident.tlp.value,
    )
    await activity.record(
        db,
        incident,
        user,
        "misp_pushed",
        f"{user.name} {PUSH_WORDS[pushed.action]} MISP",
        outcome=pushed.action,
        event=pushed.uuid,
        tlp=incident.tlp.value,
    )
    await activity.finish(db, user)
    return PushedOut(action=pushed.action, event_id=pushed.event_id, uuid=pushed.uuid, url=pushed.url)


# ---- the installation's MISP (admin) -----------------------------------------------------------


def _admin(user: User) -> None:
    if problem := policy.can_manage_misp(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, problem)


async def _out(db: AsyncSession, settings: MispSettings | None) -> MispOut:
    sending = (await licence.current(db)).has(licence.AUTOMATION)
    if settings is None:
        return MispOut(configured=False, sending=sending, distributions=misp.DISTRIBUTIONS)
    return MispOut(
        configured=True,
        url=settings.url,
        verify_tls=settings.verify_tls,
        distribution=settings.distribution,
        has_key=bool(settings.api_key),
        sending=sending,
        distributions=misp.DISTRIBUTIONS,
        version=settings.version,
    )


@router.get("/api/misp", response_model=MispOut)
async def get_misp(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)) -> MispOut:
    _admin(user)
    return await _out(db, await _settings(db))


@router.put("/api/misp", response_model=MispOut)
async def set_misp(
    body: MispIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> MispOut:
    """The key is stored encrypted and never returned; leave it out to keep the one stored."""
    _admin(user)
    url = body.url.strip().rstrip("/")
    if problem := webhooks.url_problem(url):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem.replace("webhooks", "requests"))
    settings = await _settings(db)
    key = (body.api_key or "").strip()
    if settings is None:
        if not key:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Give MISP's API key.")
        settings = MispSettings(id=1, url=url, api_key=encrypt(key), version=1)
        db.add(settings)
        changes = ["url", "api_key"]
    else:
        check_version(settings.version, body.version, "MISP settings")
        changes = [
            f
            for f, new in (("url", url), ("verify_tls", body.verify_tls), ("distribution", body.distribution))
            if getattr(settings, f) != new
        ]
        if key:
            changes.append("api_key")
        settings.version += 1
    settings.url = url
    settings.verify_tls = body.verify_tls
    settings.distribution = body.distribution
    settings.updated_by = user.id
    if key:
        settings.api_key = encrypt(key)
    audit.record(
        db,
        "misp_configured",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        url=url,
        verify_tls=body.verify_tls,
        changes=sorted(changes),
    )
    await db.commit()
    return await _out(db, settings)


@router.delete("/api/misp", status_code=status.HTTP_204_NO_CONTENT)
async def remove_misp(
    request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> Response:
    _admin(user)
    settings = await _settings(db)
    if settings is not None:
        audit.record(db, "misp_removed", actor_id=user.id, ip=sessions.client_ip(request), url=settings.url)
        await db.delete(settings)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/misp/check", response_model=MispCheckOut)
async def check_misp(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> MispCheckOut:
    """Ask the instance for its version with the stored key. A request to another system, so it
    needs `automation`, as a webhook's test send does."""
    _admin(user)
    settings = await _settings(db)
    if settings is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No MISP instance is set up.")
    if not (await licence.current(db)).has(licence.AUTOMATION):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, NOT_PUSHING)
    try:
        version = await misp.check(settings.url, decrypt(settings.api_key), settings.verify_tls)
    except misp.MispProblem as exc:
        return MispCheckOut(ok=False, error=str(exc))
    return MispCheckOut(ok=True, version=version)
