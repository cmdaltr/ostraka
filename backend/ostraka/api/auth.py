"""Signing in and out. Every account has TOTP: a password alone never opens a session."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit, notify, profile
from ostraka.auth import sessions, tokens, totp
from ostraka.auth.deps import browser_only
from ostraka.auth.secrets import decrypt, normalise_backup_code, token_hash, verify_password
from ostraka.config import get_settings
from ostraka.db import get_session
from ostraka.models import BackupCode, NotificationKind, Session, User, utcnow
from ostraka.schemas import LoginIn, LoginStep, MfaIn, TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

# One message for every failure, so the response never says whether an email has an account
WRONG = (
    "That email and password don't match, or the account is locked for a few minutes "
    "after too many attempts."
)
WRONG_CODE = "That code didn't work. Check the time on your device, or use a backup code."


def _locked(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > utcnow()


def _failed(db: AsyncSession, user: User, ip: str, action: str) -> None:
    settings = get_settings()
    user.failed_logins += 1
    if user.failed_logins >= settings.max_failed_logins:
        user.locked_until = utcnow() + timedelta(minutes=settings.lockout_minutes)
        user.failed_logins = 0
        audit.record(db, "account_locked", subject_id=user.id, ip=ip)
    audit.record(db, action, subject_id=user.id, ip=ip)


@router.post("/login", response_model=LoginStep)
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_session)) -> LoginStep:
    """Check the password. Success returns a five-minute ticket for the TOTP step, not a session."""
    ip = sessions.client_ip(request)
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    password_ok = verify_password(body.password, user.password_hash if user else None)
    if user is None or not user.is_active or _locked(user) or not password_ok:
        if user is not None and user.is_active and not _locked(user):
            _failed(db, user, ip, "login_failed")
            await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, WRONG)
    return LoginStep(ticket=tokens.mfa_ticket(user.id))


async def _use_backup_code(db: AsyncSession, user: User, code: str) -> bool:
    candidates = await db.scalars(
        select(BackupCode).where(BackupCode.user_id == user.id, BackupCode.used_at.is_(None))
    )
    normalised = normalise_backup_code(code)
    for candidate in candidates:
        if verify_password(normalised, candidate.code_hash):
            candidate.used_at = utcnow()
            return True
    return False


@router.post("/mfa", response_model=TokenOut)
async def mfa(
    body: MfaIn, request: Request, response: Response, db: AsyncSession = Depends(get_session)
) -> dict:
    """Second step: a TOTP code, or a backup code. Opens the session and sets the refresh cookie."""
    ip = sessions.client_ip(request)
    user_id = tokens.read_mfa_ticket(body.ticket)
    user = await db.get(User, user_id) if user_id else None
    if user is None or not user.is_active or _locked(user):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your sign-in took too long. Start again.")

    digits = "".join(ch for ch in body.code if ch.isdigit())
    used_backup = False
    if len(digits) == 6 and len(body.code.strip()) <= 7:
        step = totp.matching_step(decrypt(user.totp_secret), digits, user.totp_last_step)
        ok = step is not None
        if ok:
            user.totp_last_step = step
    else:
        ok = used_backup = await _use_backup_code(db, user, body.code)

    if not ok:
        _failed(db, user, ip, "mfa_failed")
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, WRONG_CODE)

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    new_device = await _new_device(db, user, request)
    grant = await sessions.start(db, user, request, response)
    if used_backup:
        left = await db.scalar(
            select(func.count()).where(BackupCode.user_id == user.id, BackupCode.used_at.is_(None))
        )
        audit.record(db, "backup_code_used", actor_id=user.id, ip=ip, remaining=left)
    audit.record(db, "login", actor_id=user.id, ip=ip, **({"new_device": True} if new_device else {}))
    if new_device:
        notify.send(
            db,
            [user],
            NotificationKind.NEW_DEVICE,
            f"New sign-in: {profile.describe_agent(new_device)} from {ip or 'an unknown address'}",
            body="Not you? Change your password and sign that browser out under Profile, Security.",
            link="/profile?tab=security",
        )
    await db.commit()
    await notify.dispatch(db)
    return grant


async def _new_device(db: AsyncSession, user: User, request: Request) -> str | None:
    """The browser's User-Agent when this account has signed in before but never from that kind of
    browser. Browsers are compared by name ("Firefox on macOS"), not the raw header, which changes
    with every version and would raise an alert after each update."""
    agent = (request.headers.get("user-agent") or "")[:255]
    known = set((await db.scalars(select(Session.user_agent).where(Session.user_id == user.id))).all())
    if not known:
        return None  # a first sign-in is expected, not news
    if profile.describe_agent(agent) in {profile.describe_agent(a) for a in known}:
        return None
    return agent or "an unnamed browser"


@router.post("/refresh", response_model=TokenOut, dependencies=[Depends(browser_only)])
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
    ostraka_refresh: str | None = Cookie(default=None),
) -> dict:
    """A new access token from the refresh cookie, which rotates every time."""
    rotated = await sessions.rotate(db, ostraka_refresh, request) if ostraka_refresh else None
    await db.commit()  # also keeps a family revocation when an old token was replayed
    if rotated is None:
        sessions.clear_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    user, session, fresh = rotated
    sessions.set_cookie(response, fresh)
    return sessions.grant(user, session.id)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(browser_only)],
)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
    ostraka_refresh: str | None = Cookie(default=None),
) -> None:
    if ostraka_refresh:
        session = await db.scalar(select(Session).where(Session.token_hash == token_hash(ostraka_refresh)))
        if session is not None:
            await sessions.revoke_family(db, session.family)
            audit.record(db, "logout", actor_id=session.user_id, ip=sessions.client_ip(request))
            await db.commit()
    sessions.clear_cookie(response)
