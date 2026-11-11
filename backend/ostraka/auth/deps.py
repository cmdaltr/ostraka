"""Request dependencies: who is calling, and with what rights."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import branding
from ostraka.auth import sessions, tokens
from ostraka.auth.secrets import token_hash
from ostraka.db import get_session
from ostraka.models import ApiToken, Role, Session, TokenScope, User, utcnow

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass
class Caller:
    user: User
    session: Session | None = None  # a browser session
    token: ApiToken | None = None  # or an API token

    @property
    def read_only(self) -> bool:
        return self.token is not None and self.token.scope is TokenScope.MCP


def _unauthorised(detail: str = "Sign in to continue.") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


async def _from_api_token(db: AsyncSession, raw: str) -> Caller:
    token = await db.scalar(select(ApiToken).where(ApiToken.token_hash == token_hash(raw)))
    now = utcnow()
    if token is None or token.revoked_at or (token.expires_at and token.expires_at <= now):
        raise _unauthorised("That API token isn't valid.")
    user = await db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise _unauthorised("That API token isn't valid.")
    if token.last_used_at is None or now - token.last_used_at > timedelta(minutes=1):
        token.last_used_at = now
        await db.commit()
    return Caller(user=user, token=token)


async def current_caller(
    request: Request,
    db: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> Caller:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorised()
    raw = authorization[7:].strip()
    if raw.startswith(branding.TOKEN_PREFIX):
        caller = await _from_api_token(db, raw)
    else:
        claims = tokens.read_access_token(raw)
        if not claims:
            raise _unauthorised("Your session has expired.")
        try:
            session_id, user_id = uuid.UUID(claims["sid"]), uuid.UUID(claims["sub"])
        except (KeyError, ValueError) as exc:
            raise _unauthorised() from exc
        session = await sessions.is_live(db, session_id)
        user = await db.get(User, user_id)
        if session is None or user is None or not user.is_active or session.user_id != user.id:
            raise _unauthorised("Your session has ended.")
        caller = Caller(user=user, session=session)
    if caller.read_only and request.method not in READ_METHODS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This token is read-only.")
    return caller


async def current_user(caller: Caller = Depends(current_caller)) -> User:
    return caller.user


def require_role(*roles: Role):
    """A dependency that lets only the given roles through."""

    async def check(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission to do that.")
        return user

    return check


async def browser_only(x_ostraka: str | None = Header(default=None)) -> None:
    """Cookie-authenticated endpoints need this header. A cross-site form can't set it, and with no
    CORS a cross-site script can't either, so it closes off CSRF on top of SameSite=Strict."""
    if x_ostraka != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing X-Ostraka header.")
