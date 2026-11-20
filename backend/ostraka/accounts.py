"""Account operations shared by the API and the CLI: invitations, resets and backup codes."""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from enum import StrEnum

from fastapi import Request
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit, licence
from ostraka.auth import policy, sessions
from ostraka.auth.secrets import hash_password, new_backup_code, new_token, token_hash
from ostraka.config import get_settings
from ostraka.models import ApiToken, BackupCode, Invitation, Role, User, utcnow

BACKUP_CODE_COUNT = 10
# The password of someone `ostraka import` created: a record of who did what, not an account anyone
# can sign in to, so it holds no place under the licence
UNUSABLE_PASSWORD = "!imported"  # noqa: S105 (a marker that no hash ever equals, not a password)
# Wrong authenticator codes an invitation takes before it closes; a reset counts towards the
# account's sign-in lockout instead
WRONG_CODE_LIMIT = 5
# What the first acceptance step leaves on a link. Cleared whenever the link stops working, so
# nothing chosen on a dead link is kept
CLEARED = {"pending_name": None, "pending_password_hash": None, "pending_totp_secret": None}
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$|^https?://\[[0-9A-Fa-f:.]+\](:\d{1,5})?$")


def link_base(request: Request | None = None) -> str:
    """Where a link points: OSTRAKA_PUBLIC_URL, or when that isn't set, the address the request came
    to (the page's own origin, which a browser sends with every POST; else the Host and scheme the
    trusted proxy passed on), or LOCAL_URL with no request."""
    settings = get_settings()
    if settings.public_url.strip() or request is None:
        return settings.public_origin
    origin = request.headers.get("origin", "")
    if _ORIGIN.match(origin):
        return origin
    return str(request.base_url).rstrip("/")


async def revoke_open_links(
    db: AsyncSession, condition, *, actor_id: uuid.UUID | None, reason: str = "replaced"
) -> int:
    """Revoke every open link matching `condition`, clear what was chosen on them, and record each one."""
    rows = await db.execute(
        update(Invitation)
        .where(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None), condition)
        .values(revoked_at=utcnow(), **CLEARED)
        .returning(Invitation.id, Invitation.email, Invitation.user_id)
        .execution_options(synchronize_session=False)
    )
    revoked = rows.all()
    for invitation_id, email, user_id in revoked:
        audit.record(
            db,
            "invitation_revoked",
            actor_id=actor_id,
            subject_id=user_id,
            email=email,
            invitation=str(invitation_id),
            reason=reason,
        )
    return len(revoked)


async def create_invitation(
    db: AsyncSession,
    *,
    email: str,
    role: Role,
    team_id: uuid.UUID | None,
    invited_by: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
    base: str | None = None,
) -> tuple[Invitation, str]:
    """A new invitation and its link, under `base` (`link_base()` when not given). Any earlier pending
    invitation for the same email or account is revoked, so only the newest link works."""
    now = utcnow()
    await revoke_open_links(
        db,
        Invitation.user_id == user_id
        if user_id
        else and_(Invitation.email == email.lower(), Invitation.user_id.is_(None)),
        actor_id=invited_by,
    )

    token = new_token()
    invitation = Invitation(
        email=email.lower(),
        role=role,
        team_id=team_id,
        user_id=user_id,
        token_hash=token_hash(token),
        invited_by=invited_by,
        expires_at=now + timedelta(hours=get_settings().invitation_hours),
    )
    db.add(invitation)
    await db.flush()
    await db.refresh(invitation, ["team"])
    # After #, which a browser never sends: the token reaches no server or proxy log
    return invitation, f"{base or link_base()}/invite#{token}"


async def revoke_api_tokens(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke every live API token the account has; returns how many."""
    result = await db.execute(
        update(ApiToken)
        .where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    return result.rowcount or 0


async def reset_access(
    db: AsyncSession, user: User, *, invited_by: uuid.UUID | None, base: str | None = None
) -> tuple[Invitation, str, int]:
    """For someone who has lost their password, authenticator or backup codes: a one-time link to
    choose a new password and enrol a new authenticator, within a day. Their sign-ins end and
    their API tokens are revoked now (a token made by whoever had the account mustn't outlast the
    reset), and any earlier reset link stops working. Returns the invitation, the link and how many
    tokens were revoked."""
    invitation, link = await create_invitation(
        db,
        email=user.email,
        role=user.role,
        team_id=user.team_id,
        invited_by=invited_by,
        user_id=user.id,
        base=base,
    )
    await sessions.revoke_all(db, user.id)
    return invitation, link, await revoke_api_tokens(db, user.id)


async def self_service_reset(db: AsyncSession, user: User) -> tuple[Invitation, str] | None:
    """Forgot password: a reset link the person asked for themselves, or None when an admin's (or the
    CLI's) reset is already open for them, which this never replaces. Nothing ends now: anyone can
    ask on someone's behalf, so sessions and tokens end only when the link is used, and the old
    password and authenticator work until then."""
    admins_open = await db.scalar(
        select(Invitation.id).where(
            Invitation.user_id == user.id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > utcnow(),
            or_(Invitation.invited_by.is_(None), Invitation.invited_by != user.id),
        )
    )
    if admins_open is not None:
        return None
    return await create_invitation(
        db, email=user.email, role=user.role, team_id=user.team_id, invited_by=user.id, user_id=user.id
    )


class LinkState(StrEnum):
    OPEN = "open"
    EXPIRED = "expired"
    CLOSED = "closed"  # too many wrong codes
    GONE = "gone"  # used, revoked, replaced or unknown


async def look_up(db: AsyncSession, token: str) -> tuple[Invitation | None, LinkState]:
    """The link a token belongs to and whether it can still be used. An expired link's pending
    choices are cleared here (the caller commits)."""
    invitation = await db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(token)))
    if invitation is None or invitation.accepted_at is not None:
        return None, LinkState.GONE
    if invitation.revoked_at is not None:
        closed = invitation.user_id is None and invitation.wrong_codes >= WRONG_CODE_LIMIT
        return None, LinkState.CLOSED if closed else LinkState.GONE
    if invitation.expires_at <= utcnow():
        for field, value in CLEARED.items():
            setattr(invitation, field, value)
        return None, LinkState.EXPIRED
    return invitation, LinkState.OPEN


async def claim(db: AsyncSession, invitation_id: uuid.UUID) -> bool:
    """Mark a link used, if it still can be: one conditional UPDATE, so of two completions at once
    only one changes the row and the other gives up."""
    result = await db.execute(
        update(Invitation)
        .where(
            Invitation.id == invitation_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > utcnow(),
        )
        .values(accepted_at=utcnow(), **CLEARED)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def wrong_code(db: AsyncSession, invitation_id: uuid.UUID) -> bool:
    """Count a wrong code on an invitation; True when that closes it (the caller audits)."""
    count = await db.scalar(
        update(Invitation)
        .where(Invitation.id == invitation_id, Invitation.revoked_at.is_(None))
        .values(wrong_codes=Invitation.wrong_codes + 1)
        .returning(Invitation.wrong_codes)
        .execution_options(synchronize_session=False)
    )
    if count is None or count < WRONG_CODE_LIMIT:
        return False
    await db.execute(
        update(Invitation)
        .where(Invitation.id == invitation_id)
        .values(revoked_at=utcnow(), **CLEARED)
        .execution_options(synchronize_session=False)
    )
    return True


async def replace_backup_codes(db: AsyncSession, user: User) -> list[str]:
    """Issue a fresh set of backup codes; every earlier code stops working."""
    await db.execute(delete(BackupCode).where(BackupCode.user_id == user.id))
    codes = [new_backup_code() for _ in range(BACKUP_CODE_COUNT)]
    db.add_all(BackupCode(user_id=user.id, code_hash=hash_password(code)) for code in codes)
    return codes


def _holds_place():
    return and_(User.is_active.is_(True), User.password_hash != UNUSABLE_PASSWORD)


def holds_place(user: User) -> bool:
    return user.is_active and user.password_hash != UNUSABLE_PASSWORD


async def places_held(db: AsyncSession) -> tuple[int, int]:
    """Places taken under the licence, and how many by admins or team leads: active accounts that can
    sign in, and open invitations that would add one (a new account, or access for someone imported)."""
    leader = User.role.in_(policy.LEADERS)
    accounts_held = (
        await db.execute(select(func.count(), func.count().filter(leader)).where(_holds_place()))
    ).one()
    holder = select(User.id).where(_holds_place())
    open_invitations = (
        await db.execute(
            select(func.count(), func.count().filter(Invitation.role.in_(policy.LEADERS))).where(
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > utcnow(),
                or_(Invitation.user_id.is_(None), Invitation.user_id.not_in(holder)),
            )
        )
    ).one()
    return accounts_held[0] + open_invitations[0], accounts_held[1] + open_invitations[1]


async def seat_refusal(db: AsyncSession, role: Role) -> str | None:
    """Why one more person who can sign in, as `role`, would go past the licence, or None."""
    standing = await licence.current(db)
    held, leaders = await places_held(db)
    return policy.seat_refusal(
        seats=standing.seats,
        held=held,
        leaders=leaders,
        role=role,
        free=standing.edition is licence.Edition.FREE,
    )


async def active_admins(db: AsyncSession) -> int:
    return len(
        (await db.scalars(select(User.id).where(User.role == Role.ADMIN, User.is_active.is_(True)))).all()
    )
