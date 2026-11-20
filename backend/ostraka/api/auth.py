"""Signing in and out. Every account has TOTP: a password alone never opens a session."""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import accounts, audit, notify, profile
from ostraka.auth import lockout, sessions, tokens, totp
from ostraka.auth.deps import browser_only
from ostraka.auth.secrets import decrypt, normalise_backup_code, token_hash, verify_password
from ostraka.config import get_settings
from ostraka.db import get_session
from ostraka.models import BackupCode, NotificationKind, Session, User, utcnow
from ostraka.notify import channels
from ostraka.schemas import ForgotAvailable, ForgotIn, ForgotOut, LoginIn, LoginStep, MfaIn, TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

# One message for every failure, so the response never says whether an email has an account
WRONG = (
    "That email and password don't match, or the account is locked for a few minutes "
    "after too many attempts."
)
WRONG_CODE = "That code didn't work. Check the time on your device, or use a backup code."
# Forgot password says the same whether or not the address has an account
FORGOT_SENT = "If that address has an account, a link is on its way. It works once, within {hours} hours."
NO_EMAIL = "Ostraka can't send email here, so ask an administrator to reset your access."
TOO_MANY = "Too many requests. Try again later."
# Requests for a reset link, per address asked about and per address asking, in FORGOT_WINDOW
FORGOT_PER_EMAIL = 3
FORGOT_PER_IP = 10
FORGOT_WINDOW = 3600


async def _failed(db: AsyncSession, user: User, ip: str, action: str) -> None:
    """A wrong password or code, counted against this account from this address (`auth.lockout`)."""
    if await lockout.failed(user.id, ip):
        audit.record(db, "account_locked", subject_id=user.id, ip=ip)
    audit.record(db, action, subject_id=user.id, ip=ip)


@router.post("/login", response_model=LoginStep)
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_session)) -> LoginStep:
    """Check the password. Success returns a five-minute ticket for the TOTP step, not a session."""
    ip = sessions.client_ip(request)
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    password_ok = verify_password(body.password, user.password_hash if user else None)
    locked = user is not None and await lockout.locked(user.id, ip)
    if user is None or not user.is_active or locked or not password_ok:
        if user is not None and user.is_active and not locked:
            await _failed(db, user, ip, "login_failed")
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
    if user is None or not user.is_active or await lockout.locked(user.id, ip):
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
        await _failed(db, user, ip, "mfa_failed")
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, WRONG_CODE)

    await lockout.clear(user.id, ip)
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


def _forgot_available() -> bool:
    """Email has to be set up, and OSTRAKA_PUBLIC_URL too: an emailed link built from the request's
    Host header would let anyone who asks send a victim a link to their own site."""
    return channels.email_enabled() and bool(get_settings().public_url.strip())


@router.get("/forgot", response_model=ForgotAvailable)
async def forgot_available() -> dict:
    """Whether the sign-in page offers Forgot password, or says to ask an admin."""
    return {"available": _forgot_available()}


@router.post("/forgot", response_model=ForgotOut, dependencies=[Depends(browser_only)])
async def forgot(body: ForgotIn, request: Request, db: AsyncSession = Depends(get_session)) -> dict:
    """Email a reset-access link to the account with this address, if there's one and it's active.
    The answer is the same either way. Nothing ends now: sessions and tokens end, and the old
    password stops working, only when the link is used (`api/invitations.accept_complete`)."""
    if not _forgot_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_EMAIL)
    ip = sessions.client_ip(request)
    by_ip = await lockout.throttled("forgot-ip", ip, FORGOT_PER_IP, FORGOT_WINDOW)
    by_email = await lockout.throttled(
        "forgot-email", token_hash(body.email), FORGOT_PER_EMAIL, FORGOT_WINDOW
    )
    if by_ip or by_email:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
    hours = get_settings().invitation_hours
    user = await db.scalar(select(User).where(User.email == body.email))
    # Someone imported has no password to forget, and an open admin's reset is never replaced
    issued = (
        await accounts.self_service_reset(db, user)
        if user is not None and user.is_active and accounts.holds_place(user)
        else None
    )
    if issued is not None:
        _, link = issued
        audit.record(
            db, "access_reset_issued", subject_id=user.id, ip=ip, via="self-service", tokens_revoked=0
        )
        await db.commit()
        notify.email_later(
            user.email,
            "Reset your Ostraka access",
            f"A reset of the Ostraka access for {user.email} was asked for from the sign-in page "
            f"({ip or 'an unknown address'}).\n\n"
            f"Open this link to choose a new password and set up a new authenticator. It works once, "
            f"within {hours} hours:\n\n{link}\n\n"
            "If you didn't ask, ignore this email: your password and authenticator keep working, and "
            "nothing changes unless the link is used.",
        )
    return {"detail": FORGOT_SENT.format(hours=hours)}
