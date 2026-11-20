"""Inviting people, and accepting an invitation: password, then authenticator, then backup codes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import accounts, audit
from ostraka.auth import lockout, policy, sessions, totp
from ostraka.auth.deps import current_user
from ostraka.auth.secrets import decrypt, encrypt, hash_password, password_problem
from ostraka.config import get_settings
from ostraka.db import get_session
from ostraka.models import Invitation, Role, Team, User, utcnow
from ostraka.schemas import (
    AcceptCode,
    AcceptDone,
    AcceptEnrol,
    AcceptStart,
    InvitationCreated,
    InvitationIn,
    InvitationOut,
    InvitationPreview,
    InvitationToken,
)

router = APIRouter(prefix="/api/invitations", tags=["invitations"])

GONE = "This link isn't valid: it has been used, replaced or withdrawn. Ask for a new one."
EXPIRED = "This link has expired. Each one works for {hours} hours; ask for a new one."
CLOSED = "That's {limit} wrong codes, so this link has been closed. Ask whoever invited you for a new one."
LOCKED = "Too many wrong codes. Try again later."


def _out(invitation: Invitation, **extra) -> dict:
    return {
        **InvitationOut.model_validate(invitation).model_dump(),
        "is_reset": invitation.user_id is not None,
        **extra,
    }


@router.post("", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED)
async def invite(
    body: InvitationIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    if problem := policy.can_invite(user, body.role, body.team_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, problem)
    if body.team_id is not None and await db.get(Team, body.team_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That team doesn't exist.")
    if await db.scalar(select(User.id).where(User.email == body.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Someone with that email already has an account.")
    if problem := await accounts.seat_refusal(db, body.role):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, problem)

    team_id = None if body.role is Role.ADMIN and body.team_id is None else body.team_id
    invitation, link = await accounts.create_invitation(
        db,
        email=body.email,
        role=body.role,
        team_id=team_id,
        invited_by=user.id,
        base=accounts.link_base(request),
    )
    audit.record(
        db,
        "invited",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        email=body.email,
        role=body.role.value,
        team_id=str(team_id) if team_id else None,
    )
    await db.commit()
    return _out(invitation, link=link)


@router.get("", response_model=list[InvitationOut])
async def list_invitations(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[dict]:
    """Invitations not yet accepted or revoked, including expired ones, for the teams you manage."""
    if user.role not in (Role.ADMIN, Role.TEAM_LEAD):
        return []
    query = select(Invitation).where(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))
    if user.role is Role.TEAM_LEAD:
        query = query.where(Invitation.team_id == user.team_id, Invitation.user_id.is_(None))
    rows = await db.scalars(query.order_by(Invitation.created_at.desc()))
    return [_out(i) for i in rows]


@router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def revoke(
    invitation_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    invitation = await db.get(Invitation, invitation_id)
    if invitation is None or not policy.can_manage_invitations_of(user, invitation.team_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invitation.")
    if invitation.user_id is not None and user.role is not Role.ADMIN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invitation.")
    if invitation.accepted_at is None and invitation.revoked_at is None:
        invitation.revoked_at = utcnow()
        for field, value in accounts.CLEARED.items():
            setattr(invitation, field, value)
        audit.record(
            db,
            "invitation_revoked",
            actor_id=user.id,
            subject_id=invitation.user_id,
            ip=sessions.client_ip(request),
            email=invitation.email,
            invitation=str(invitation.id),
            reason="revoked",
        )
        await db.commit()


# ---- accepting: no login, the token in the link is the credential ---------------------------


async def _open(db: AsyncSession, token: str) -> tuple[Invitation, User | None]:
    """The open link a token belongs to, and the account it resets (None for an invitation). An
    expired link says so; every other dead link gets the one message, so it says nothing more."""
    invitation, state = await accounts.look_up(db, token)
    if state is accounts.LinkState.EXPIRED:
        await db.commit()  # what was chosen on it is cleared
        raise HTTPException(status.HTTP_410_GONE, EXPIRED.format(hours=get_settings().invitation_hours))
    if state is accounts.LinkState.CLOSED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, CLOSED.format(limit=accounts.WRONG_CODE_LIMIT))
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, GONE)
    reset = await db.get(User, invitation.user_id) if invitation.user_id is not None else None
    if invitation.user_id is not None and reset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, GONE)
    # Server-issued links (the CLI, the first admin) have no inviter
    inviter = await db.get(User, invitation.invited_by) if invitation.invited_by is not None else None
    if inviter is not None and (
        problem := policy.link_refusal(
            inviter, role=invitation.role, team_id=invitation.team_id, reset_of=reset
        )
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, problem)
    return invitation, reset


@router.post("/accept", response_model=InvitationPreview)
async def preview(body: InvitationToken, db: AsyncSession = Depends(get_session)) -> dict:
    invitation, _ = await _open(db, body.token)
    return {
        "email": invitation.email,
        "role": invitation.role,
        "team": invitation.team.name if invitation.team else None,
        "is_reset": invitation.user_id is not None,
        "expires_at": invitation.expires_at,
    }


@router.post("/accept/start", response_model=AcceptEnrol)
async def accept_start(body: AcceptStart, db: AsyncSession = Depends(get_session)) -> dict:
    """Name and password. Returns the authenticator secret to scan: made on the first start, and the
    same one on every later start of this link (a second tab, the link opened again), so whatever
    was scanned keeps working. A later start's name and password replace the earlier ones."""
    invitation, _ = await _open(db, body.token)
    if problem := password_problem(body.password, invitation.email, body.name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    if invitation.pending_totp_secret:
        secret = decrypt(invitation.pending_totp_secret)
    else:
        secret = totp.new_secret()
        invitation.pending_totp_secret = encrypt(secret)
    invitation.pending_name = body.name.strip()
    invitation.pending_password_hash = hash_password(body.password)
    await db.commit()
    uri = totp.provisioning_uri(secret, invitation.email)
    return {"otpauth_uri": uri, "qr_svg": totp.qr_svg_data_uri(uri), "secret": secret}


@router.post("/accept/complete", response_model=AcceptDone)
async def accept_complete(
    body: AcceptCode,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """A code from the new authenticator proves it works. Creates (or restores) the account, signs
    in, and returns the backup codes, which are shown only this once."""
    invitation, reset = await _open(db, body.token)
    name, password_hash, totp_secret = (
        invitation.pending_name,
        invitation.pending_password_hash,
        invitation.pending_totp_secret,
    )
    if not (password_hash and totp_secret and name):
        raise HTTPException(status.HTTP_409_CONFLICT, "Choose a password first.")
    ip = sessions.client_ip(request)
    # A reset is a way into an existing account, so its wrong codes count as sign-in failures do
    if reset is not None and await lockout.locked(reset.id, ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, LOCKED)
    step = totp.matching_step(decrypt(totp_secret), body.code, 0)
    if step is None:
        message = "That code didn't match. Try the next one."
        if reset is not None:
            if await lockout.failed(reset.id, ip):
                audit.record(db, "account_locked", subject_id=reset.id, ip=ip)
            audit.record(db, "access_reset_failed", subject_id=reset.id, ip=ip)
        else:
            audit.record(
                db, "invitation_wrong_code", ip=ip, email=invitation.email, invitation=str(invitation.id)
            )
            if await accounts.wrong_code(db, invitation.id):
                audit.record(
                    db,
                    "invitation_closed",
                    ip=ip,
                    email=invitation.email,
                    invitation=str(invitation.id),
                    wrong_codes=accounts.WRONG_CODE_LIMIT,
                )
                message = CLOSED.format(limit=accounts.WRONG_CODE_LIMIT)
        await db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)

    if reset is None and await db.scalar(select(User.id).where(User.email == invitation.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That email already has an account.")
    # Claimed before anything else changes: of two completions at once, the second stops here
    if not await accounts.claim(db, invitation.id):
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, GONE)

    revoked = 0
    if reset is not None:
        user = reset
        await sessions.revoke_all(db, user.id)
        # Any made with the old sign-in since the link was issued
        revoked = await accounts.revoke_api_tokens(db, user.id)
        # An authenticator change begun from the account before the reset mustn't finish after it
        user.pending_totp_secret = None
        action = "access_reset_completed"
    else:
        user = User(email=invitation.email, role=invitation.role, team_id=invitation.team_id)
        db.add(user)
        action = "account_created"

    user.name = name
    user.password_hash = password_hash
    user.totp_secret = totp_secret
    user.totp_last_step = step
    await lockout.clear(user.id)  # from every address: the person has proved who they are afresh
    user.last_login_at = utcnow()
    await db.flush()

    codes = await accounts.replace_backup_codes(db, user)
    grant = await sessions.start(db, user, request, response)
    detail: dict = {"tokens_revoked": revoked} if reset is not None else {}
    if reset is not None and invitation.invited_by == reset.id:
        detail["via"] = "self-service"
    audit.record(
        db,
        action,
        actor_id=user.id,
        subject_id=user.id,
        ip=ip,
        invited_by=str(invitation.invited_by),
        **detail,
    )
    await db.commit()
    return {**grant, "backup_codes": codes}
