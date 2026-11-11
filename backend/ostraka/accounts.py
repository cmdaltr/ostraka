"""Account operations shared by the API and the CLI: invitations and backup codes."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import licence
from ostraka.auth import policy
from ostraka.auth.secrets import hash_password, new_backup_code, new_token, token_hash
from ostraka.config import get_settings
from ostraka.models import BackupCode, Invitation, Role, User, utcnow

BACKUP_CODE_COUNT = 10
# The password of someone `ostraka import` created: a record of who did what, not an account anyone
# can sign in to, so it holds no place under the licence
UNUSABLE_PASSWORD = "!imported"  # noqa: S105 (a marker that no hash ever equals, not a password)


async def create_invitation(
    db: AsyncSession,
    *,
    email: str,
    role: Role,
    team_id: uuid.UUID | None,
    invited_by: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
) -> tuple[Invitation, str]:
    """A new invitation and its link. Any earlier pending invitation for the same email or account
    is revoked, so only the newest link works."""
    now = utcnow()
    pending = update(Invitation).where(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))
    pending = (
        pending.where(Invitation.user_id == user_id)
        if user_id
        else pending.where(Invitation.email == email, Invitation.user_id.is_(None))
    )
    await db.execute(pending.values(revoked_at=now))

    token = new_token()
    invitation = Invitation(
        email=email.lower(),
        role=role,
        team_id=team_id,
        user_id=user_id,
        token_hash=token_hash(token),
        invited_by=invited_by,
        expires_at=now + timedelta(days=get_settings().invitation_days),
    )
    db.add(invitation)
    await db.flush()
    await db.refresh(invitation, ["team"])
    return invitation, f"{get_settings().public_url.rstrip('/')}/invite/{token}"


async def find_open_invitation(db: AsyncSession, token: str) -> Invitation | None:
    invitation = await db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(token)))
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= utcnow()
    ):
        return None
    return invitation


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
