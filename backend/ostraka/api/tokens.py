"""Personal API tokens (`ost_…`), for scripts and the MCP server. Created only from a browser."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit, branding
from ostraka.auth import sessions
from ostraka.auth.deps import Caller, current_caller
from ostraka.auth.secrets import new_token, token_hash
from ostraka.db import get_session
from ostraka.models import ApiToken, utcnow
from ostraka.schemas import ApiTokenCreated, ApiTokenIn, ApiTokenOut

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


@router.get("", response_model=list[ApiTokenOut])
async def list_tokens(caller: Caller = Depends(current_caller), db: AsyncSession = Depends(get_session)):
    rows = await db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == caller.user.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    return list(rows.all())


@router.post("", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: ApiTokenIn,
    request: Request,
    caller: Caller = Depends(current_caller),
    db: AsyncSession = Depends(get_session),
) -> dict:
    if caller.session is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Create tokens from the browser, not with another token."
        )
    raw = branding.TOKEN_PREFIX + new_token(30)
    token = ApiToken(
        user_id=caller.user.id,
        name=body.name.strip(),
        prefix=raw[: len(branding.TOKEN_PREFIX) + 6],
        token_hash=token_hash(raw),
        scope=body.scope,
        expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(token)
    audit.record(
        db,
        "api_token_created",
        actor_id=caller.user.id,
        ip=sessions.client_ip(request),
        name=token.name,
        scope=token.scope.value,
    )
    await db.commit()
    await db.refresh(token)
    return {**ApiTokenOut.model_validate(token).model_dump(), "token": raw}


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def revoke_token(
    token_id: uuid.UUID,
    request: Request,
    caller: Caller = Depends(current_caller),
    db: AsyncSession = Depends(get_session),
) -> None:
    token = await db.get(ApiToken, token_id)
    if token is None or token.user_id != caller.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such token.")
    if token.revoked_at is None:
        token.revoked_at = utcnow()
        audit.record(
            db, "api_token_revoked", actor_id=caller.user.id, ip=sessions.client_ip(request), name=token.name
        )
        await db.commit()
