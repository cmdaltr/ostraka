"""Your own account, the people directory, and admins' changes to other accounts."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import accounts, audit, profile
from ostraka.auth import policy, sessions, totp
from ostraka.auth.deps import Caller, current_caller, current_user
from ostraka.auth.secrets import decrypt, encrypt, hash_password, password_problem, verify_password
from ostraka.db import get_session
from ostraka.incidents.views import check_version
from ostraka.models import BackupCode, Role, SecurityEvent, Session, Team, User, utcnow
from ostraka.schemas import (
    AcceptEnrol,
    AccountPatch,
    AuthenticatorStart,
    BackupCodesOut,
    CodeIn,
    InvitationCreated,
    InvitationOut,
    MeOut,
    MePatch,
    PasswordChange,
    SecurityEventOut,
    SessionOut,
    UserOut,
)

router = APIRouter(prefix="/api/users", tags=["users"])


async def _me(db: AsyncSession, user: User) -> MeOut:
    out = MeOut.model_validate(user)
    out.backup_codes_left = (
        await db.scalar(
            select(func.count()).where(BackupCode.user_id == user.id, BackupCode.used_at.is_(None))
        )
        or 0
    )
    return out


@router.get("/me", response_model=MeOut)
async def me(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)) -> MeOut:
    return await _me(db, user)


@router.patch("/me", response_model=MeOut)
async def update_me(
    body: MePatch, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> MeOut:
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        if field in ("quiet_from", "quiet_until"):
            value = value or None  # "" clears them
        elif isinstance(value, str) and field in ("name", "job_title"):
            value = value.strip()
        setattr(user, field, value)
    if (user.quiet_from is None) != (user.quiet_until is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Set both ends of your quiet hours, or neither."
        )
    await db.commit()
    return await _me(db, user)


def _browser(caller: Caller, what: str) -> Session:
    if caller.session is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{what} from the browser, not with a token.")
    return caller.session


@router.get("/me/sessions", response_model=list[SessionOut])
async def my_sessions(
    caller: Caller = Depends(current_caller), db: AsyncSession = Depends(get_session)
) -> list:
    """Every browser you're signed in on: the live end of each sign-in's chain of refresh tokens."""
    now = utcnow()
    rows = (
        await db.scalars(
            select(Session)
            .where(
                Session.user_id == caller.user.id,
                Session.revoked_at.is_(None),
                Session.rotated_at.is_(None),
                Session.expires_at > now,
            )
            .order_by(Session.created_at.desc())
        )
    ).all()
    started = dict(
        (
            await db.execute(
                select(Session.family, func.min(Session.created_at))
                .where(Session.family.in_([r.family for r in rows]))
                .group_by(Session.family)
            )
        ).all()
    )
    current = caller.session.family if caller.session else None
    return [
        SessionOut(
            family=r.family,
            device=profile.describe_agent(r.user_agent),
            ip=r.ip,
            signed_in_at=started.get(r.family, r.created_at),
            last_active_at=r.created_at,
            expires_at=r.expires_at,
            current=r.family == current,
        )
        for r in rows
    ]


@router.delete("/me/sessions/{family}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def end_session(
    family: uuid.UUID,
    request: Request,
    caller: Caller = Depends(current_caller),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Sign one browser out. This one signs out with Sign out, not here."""
    mine = _browser(caller, "Sign browsers out")
    if family == mine.family:
        raise HTTPException(status.HTTP_409_CONFLICT, "That's this browser: use Sign out instead.")
    owned = await db.scalar(
        select(Session.id).where(Session.family == family, Session.user_id == caller.user.id)
    )
    if owned is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such session.")
    await sessions.revoke_family(db, family)
    audit.record(db, "session_ended", actor_id=caller.user.id, ip=sessions.client_ip(request))
    await db.commit()


@router.post("/me/sessions/end-others", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def end_other_sessions(
    request: Request, caller: Caller = Depends(current_caller), db: AsyncSession = Depends(get_session)
) -> None:
    """Sign out every browser except this one."""
    mine = _browser(caller, "Sign browsers out")
    await sessions.revoke_all(db, caller.user.id, keep_family=mine.family)
    audit.record(db, "sessions_ended", actor_id=caller.user.id, ip=sessions.client_ip(request))
    await db.commit()


@router.get("/me/security-log", response_model=list[SecurityEventOut])
async def my_security_log(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list:
    """Your last 100 security events: sign-ins, failures, and changes to your account, including an
    admin's."""
    rows = (
        await db.scalars(
            select(SecurityEvent)
            .where(or_(SecurityEvent.actor_id == user.id, SecurityEvent.subject_id == user.id))
            .order_by(SecurityEvent.at.desc())
            .limit(100)
        )
    ).all()
    others = {r.actor_id for r in rows if r.actor_id and r.actor_id != user.id}
    names = (
        dict((await db.execute(select(User.id, User.name).where(User.id.in_(others)))).all())
        if others
        else {}
    )
    return [
        SecurityEventOut(
            id=r.id,
            at=r.at,
            action=r.action,
            ip=r.ip,
            by=names.get(r.actor_id) if r.actor_id in names else None,
            detail=r.detail or {},
        )
        for r in rows
    ]


@router.post("/me/authenticator", response_model=AcceptEnrol)
async def start_new_authenticator(
    body: AuthenticatorStart,
    caller: Caller = Depends(current_caller),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Begin moving to a new authenticator: your password and a code from the current one, then a
    secret to scan. The old one keeps working until the new one proves itself."""
    _browser(caller, "Change your authenticator")
    user = caller.user
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your password isn't right.")
    step = totp.matching_step(decrypt(user.totp_secret), body.code, user.totp_last_step)
    if step is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "That code from your current authenticator didn't work."
        )
    user.totp_last_step = step
    secret = totp.new_secret()
    user.pending_totp_secret = encrypt(secret)
    await db.commit()
    uri = totp.provisioning_uri(secret, user.email)
    return {"otpauth_uri": uri, "qr_svg": totp.qr_svg_data_uri(uri), "secret": secret}


@router.post("/me/authenticator/confirm", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def confirm_new_authenticator(
    body: CodeIn,
    request: Request,
    caller: Caller = Depends(current_caller),
    db: AsyncSession = Depends(get_session),
) -> None:
    """A code from the new authenticator swaps it in. Every other browser is signed out."""
    mine = _browser(caller, "Change your authenticator")
    user = caller.user
    if not user.pending_totp_secret:
        raise HTTPException(status.HTTP_409_CONFLICT, "Start again: there's no new authenticator waiting.")
    step = totp.matching_step(decrypt(user.pending_totp_secret), body.code, 0)
    if step is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That code from the new authenticator didn't work.")
    user.totp_secret, user.pending_totp_secret, user.totp_last_step = user.pending_totp_secret, None, step
    await sessions.revoke_all(db, user.id, keep_family=mine.family)
    audit.record(db, "mfa_changed", actor_id=user.id, ip=sessions.client_ip(request))
    await db.commit()


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def change_password(
    body: PasswordChange,
    request: Request,
    caller: Caller = Depends(current_caller),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Change your password. Every other signed-in browser is signed out."""
    user = caller.user
    if caller.session is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Change your password from the browser, not with a token."
        )
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your current password isn't right.")
    if problem := password_problem(body.new_password, user.email, user.name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    user.password_hash = hash_password(body.new_password)
    await sessions.revoke_all(db, user.id, keep_family=caller.session.family)
    audit.record(db, "password_changed", actor_id=user.id, ip=sessions.client_ip(request))
    await db.commit()


@router.post("/me/backup-codes", response_model=BackupCodesOut)
async def new_backup_codes(
    body: CodeIn,
    request: Request,
    caller: Caller = Depends(current_caller),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Replace your backup codes. Needs a current authenticator code, so a stolen session can't."""
    user = caller.user
    if caller.session is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Manage backup codes from the browser, not with a token."
        )
    step = totp.matching_step(decrypt(user.totp_secret), body.code, user.totp_last_step)
    if step is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That authenticator code didn't work.")
    user.totp_last_step = step
    codes = await accounts.replace_backup_codes(db, user)
    audit.record(db, "backup_codes_replaced", actor_id=user.id, ip=sessions.client_ip(request))
    await db.commit()
    return {"backup_codes": codes}


@router.get("", response_model=list[UserOut])
async def directory(
    team_id: uuid.UUID | None = None,
    viewer: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[UserOut]:
    """Everyone, for mentions and assignment. Any signed-in person can see names, teams, roles, job
    titles and availability; phones only as `policy.can_see_phone` allows."""
    query = select(User).order_by(User.name)
    if team_id is not None:
        query = query.where(User.team_id == team_id)
    people = []
    for person in (await db.scalars(query)).all():
        out = UserOut.model_validate(person)
        if not person.phone or not policy.can_see_phone(viewer, person):
            out.phone = None
        people.append(out)
    return people


async def _target(db: AsyncSession, actor: User, user_id: uuid.UUID) -> User:
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person.")
    if problem := policy.can_change_account(actor, target):
        raise HTTPException(status.HTTP_403_FORBIDDEN, problem)
    return target


@router.patch("/{user_id}", response_model=UserOut)
async def change_account(
    user_id: uuid.UUID,
    body: AccountPatch,
    request: Request,
    actor: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> User:
    target = await _target(db, actor, user_id)
    check_version(target.version, body.version, "account")
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    role = changes.get("role", target.role)
    team_id = changes.get("team_id", target.team_id)
    if role is not Role.ADMIN and team_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Everyone except an admin needs a team.")
    if team_id is not None and await db.get(Team, team_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That team doesn't exist.")
    losing_admin = (
        target.role is Role.ADMIN
        and target.is_active
        and (role is not Role.ADMIN or changes.get("is_active") is False)
    )
    if losing_admin and await accounts.active_admins(db) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ostraka needs at least one active admin.")
    reactivating = changes.get("is_active") is True and not target.is_active
    if reactivating and target.password_hash != accounts.UNUSABLE_PASSWORD:
        if problem := await accounts.seat_refusal(db, role):
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, problem)

    for field, value in changes.items():
        setattr(target, field, value)
    if changes:
        target.version += 1
    if changes.get("is_active") is False:
        await sessions.revoke_all(db, target.id)
    audit.record(
        db,
        "account_changed",
        actor_id=actor.id,
        subject_id=target.id,
        ip=sessions.client_ip(request),
        **{k: str(v) for k, v in changes.items()},
    )
    await db.commit()
    await db.refresh(target)
    return target


@router.post("/{user_id}/reset-access", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED)
async def reset_access(
    user_id: uuid.UUID,
    request: Request,
    actor: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """For someone who has lost their authenticator and backup codes: a one-time link to choose a
    new password and enrol a new authenticator. Their existing sign-ins end now."""
    target = await _target(db, actor, user_id)
    # Someone imported, being given a way in for the first time, takes a place
    if target.is_active and not accounts.holds_place(target):
        if problem := await accounts.seat_refusal(db, target.role):
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, problem)
    invitation, link = await accounts.create_invitation(
        db,
        email=target.email,
        role=target.role,
        team_id=target.team_id,
        invited_by=actor.id,
        user_id=target.id,
    )
    await sessions.revoke_all(db, target.id)
    audit.record(
        db, "access_reset_issued", actor_id=actor.id, subject_id=target.id, ip=sessions.client_ip(request)
    )
    await db.commit()
    return {**InvitationOut.model_validate(invitation).model_dump(), "is_reset": True, "link": link}
