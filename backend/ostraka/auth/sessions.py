"""Browser sessions: issue, rotate and revoke refresh tokens, and set the cookie that carries them."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.auth import tokens
from ostraka.auth.secrets import new_token, token_hash
from ostraka.config import Environment, get_settings
from ostraka.models import Session, User, utcnow

COOKIE = "ostraka_refresh"
COOKIE_PATH = "/api/auth"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def set_cookie(response: Response, refresh: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE,
        refresh,
        max_age=settings.session_days * 86400,
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=settings.env is not Environment.TEST and settings.public_url.startswith("https://"),
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path=COOKIE_PATH)


async def start(db: AsyncSession, user: User, request: Request, response: Response) -> dict:
    """Open a session for `user`: set the refresh cookie, return the access token."""
    refresh = new_token()
    session = Session(
        user_id=user.id,
        family=uuid.uuid4(),
        token_hash=token_hash(refresh),
        expires_at=utcnow() + timedelta(days=get_settings().session_days),
        user_agent=(request.headers.get("user-agent") or "")[:255],
        ip=client_ip(request),
    )
    db.add(session)
    await db.flush()
    set_cookie(response, refresh)
    return grant(user, session.id)


def grant(user: User, session_id: uuid.UUID) -> dict:
    settings = get_settings()
    return {
        "access_token": tokens.access_token(user.id, user.role, session_id),
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
    }


async def rotate(db: AsyncSession, refresh: str, request: Request) -> tuple[User, Session, str] | None:
    """Swap a refresh token for a new one. Presenting an already-rotated token revokes its family."""
    now = utcnow()
    current = await db.scalar(select(Session).where(Session.token_hash == token_hash(refresh)))
    if current is None:
        return None
    if current.rotated_at is not None or current.revoked_at is not None:
        if current.rotated_at is not None:
            await revoke_family(db, current.family)
        return None
    if current.expires_at <= now:
        return None
    user = await db.get(User, current.user_id)
    if user is None or not user.is_active:
        return None

    current.rotated_at = now
    fresh_token = new_token()
    fresh = Session(
        user_id=user.id,
        family=current.family,
        token_hash=token_hash(fresh_token),
        expires_at=current.expires_at,  # rotation never extends a session beyond its original life
        user_agent=(request.headers.get("user-agent") or "")[:255],
        ip=client_ip(request),
    )
    db.add(fresh)
    await db.flush()
    return user, fresh, fresh_token


async def revoke_family(db: AsyncSession, family: uuid.UUID) -> None:
    await db.execute(
        update(Session)
        .where(Session.family == family, Session.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_all(db: AsyncSession, user_id: uuid.UUID, keep_family: uuid.UUID | None = None) -> None:
    query = update(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
    if keep_family is not None:
        query = query.where(Session.family != keep_family)
    await db.execute(query.values(revoked_at=utcnow()))


async def is_live(db: AsyncSession, session_id: uuid.UUID) -> Session | None:
    """The session behind an access token, if it hasn't been revoked. Rotation keeps a family live."""
    session = await db.get(Session, session_id)
    if session is None or session.revoked_at is not None or session.expires_at <= utcnow():
        return None
    return session
