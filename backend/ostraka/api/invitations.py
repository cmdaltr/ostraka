"""Inviting people, and accepting an invitation: password, then authenticator, then backup codes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import accounts, audit
from ostraka.auth import policy, sessions, totp
from ostraka.auth.deps import current_user
from ostraka.auth.secrets import decrypt, encrypt, hash_password, password_problem
from ostraka.db import get_session
from ostraka.models import Invitation, Role, Team, User, utcnow
from ostraka.schemas import (
    AcceptDone,
    AcceptEnrol,
    AcceptStart,
    CodeIn,
    InvitationCreated,
    InvitationIn,
    InvitationOut,
    InvitationPreview,
)

router = APIRouter(prefix="/api/invitations", tags=["invitations"])

GONE = "This invitation has expired, been replaced, or already been used. Ask for a new one."


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
        db, email=body.email, role=body.role, team_id=team_id, invited_by=user.id
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
        audit.record(
            db, "invitation_revoked", actor_id=user.id, ip=sessions.client_ip(request), email=invitation.email
        )
        await db.commit()


# ---- accepting: no login, the token in the link is the credential ---------------------------


async def _open(db: AsyncSession, token: str) -> Invitation:
    invitation = await accounts.find_open_invitation(db, token)
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, GONE)
    return invitation


@router.get("/accept/{token}", response_model=InvitationPreview)
async def preview(token: str, db: AsyncSession = Depends(get_session)) -> dict:
    invitation = await _open(db, token)
    return {
        "email": invitation.email,
        "role": invitation.role,
        "team": invitation.team.name if invitation.team else None,
        "is_reset": invitation.user_id is not None,
        "expires_at": invitation.expires_at,
    }


@router.post("/accept/{token}/start", response_model=AcceptEnrol)
async def accept_start(token: str, body: AcceptStart, db: AsyncSession = Depends(get_session)) -> dict:
    """Name and password. Returns a new authenticator secret to scan; starting again replaces it."""
    invitation = await _open(db, token)
    if problem := password_problem(body.password, invitation.email, body.name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    secret = totp.new_secret()
    invitation.pending_name = body.name.strip()
    invitation.pending_password_hash = hash_password(body.password)
    invitation.pending_totp_secret = encrypt(secret)
    await db.commit()
    uri = totp.provisioning_uri(secret, invitation.email)
    return {"otpauth_uri": uri, "qr_svg": totp.qr_svg_data_uri(uri), "secret": secret}


@router.post("/accept/{token}/complete", response_model=AcceptDone)
async def accept_complete(
    token: str,
    body: CodeIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """A code from the new authenticator proves it works. Creates (or restores) the account, signs
    in, and returns the backup codes, which are shown only this once."""
    invitation = await _open(db, token)
    if not (invitation.pending_password_hash and invitation.pending_totp_secret and invitation.pending_name):
        raise HTTPException(status.HTTP_409_CONFLICT, "Choose a password first.")
    step = totp.matching_step(decrypt(invitation.pending_totp_secret), body.code, 0)
    if step is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That code didn't match. Try the next one.")

    ip = sessions.client_ip(request)
    if invitation.user_id is not None:
        user = await db.get(User, invitation.user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, GONE)
        await sessions.revoke_all(db, user.id)
        action = "access_reset_completed"
    else:
        if await db.scalar(select(User.id).where(User.email == invitation.email)):
            raise HTTPException(status.HTTP_409_CONFLICT, "That email already has an account.")
        user = User(email=invitation.email, role=invitation.role, team_id=invitation.team_id)
        db.add(user)
        action = "account_created"

    user.name = invitation.pending_name
    user.password_hash = invitation.pending_password_hash
    user.totp_secret = invitation.pending_totp_secret
    user.totp_last_step = step
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    await db.flush()

    invitation.accepted_at = utcnow()
    invitation.pending_password_hash = invitation.pending_totp_secret = None
    codes = await accounts.replace_backup_codes(db, user)
    grant = await sessions.start(db, user, request, response)
    audit.record(
        db, action, actor_id=user.id, subject_id=user.id, ip=ip, invited_by=str(invitation.invited_by)
    )
    await db.commit()
    return {**grant, "backup_codes": codes}
