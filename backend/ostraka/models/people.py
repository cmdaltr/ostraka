"""People: teams, accounts, invitations, sessions, API tokens and the security log."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ostraka.db import Base

# Set only by `ostraka import`, which replays a record's history at the times the file gives, so the
# hash-chained activity log is written in order rather than rewritten afterwards
clock: ContextVar[datetime | None] = ContextVar("clock", default=None)


def utcnow() -> datetime:
    """Now, or the importer's replay time while it's loading a record."""
    return clock.get() or datetime.now(UTC)


def _enum(cls: type[StrEnum], name: str) -> Enum:
    # Stored as a checked string, so adding a member is a one-line migration, not a type change
    return Enum(cls, name=name, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e])


class Role(StrEnum):
    """What an account may do across ostraka. A team lead leads their home team."""

    ADMIN = "admin"
    TEAM_LEAD = "team_lead"
    RESPONDER = "responder"
    OBSERVER = "observer"


class Availability(StrEnum):
    """Whether someone can be asked to pick up work right now, shown beside their name."""

    AVAILABLE = "available"
    BUSY = "busy"  # on something already: ask before handing them more
    AWAY = "away"  # off duty or on leave


class TokenScope(StrEnum):
    FULL = "full"
    MCP = "mcp"  # read-only, for the MCP server (phase 5)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    code: Mapped[str | None] = mapped_column(String(8), unique=True)  # SOC, FOR, IR, IM, CTI, CTH, SLT
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)  # other names: Intel., Int.
    # The team channel's Workflows webhook URL, encrypted: anyone holding it can post to the channel
    teams_webhook: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    members: Mapped[list[User]] = relationship(back_populates="team")

    @property
    def has_channel(self) -> bool:
        return bool(self.teams_webhook)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # always lower-case
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(_enum(Role, "role"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # TOTP is mandatory; the secret is encrypted at rest (ostraka.auth.crypto)
    totp_secret: Mapped[str] = mapped_column(Text)
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)  # refuses a code being replayed

    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Profile: what colleagues see beside the name (the phone only if they aren't observers)
    job_title: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")  # out of hours, for incidents
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/London")
    availability: Mapped[Availability] = mapped_column(
        _enum(Availability, "availability"), default=Availability.AVAILABLE
    )

    # A replacement authenticator, waiting for its first code (encrypted, as totp_secret is)
    pending_totp_secret: Mapped[str | None] = mapped_column(Text)

    # Preferences
    theme_palette: Mapped[str] = mapped_column(String(16), default="cobalt")
    theme_mode: Mapped[str] = mapped_column(String(16), default="system")
    show_raw_iocs: Mapped[bool] = mapped_column(Boolean, default=False)  # only changes what observers see
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_push: Mapped[bool] = mapped_column(Boolean, default=True)
    # Kinds that stay under the bell only: no email or push
    muted_kinds: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    # Quiet hours, "HH:MM" in the person's own time zone; email and push wait unless it's critical
    quiet_from: Mapped[str | None] = mapped_column(String(5))
    quiet_until: Mapped[str | None] = mapped_column(String(5))
    new_device_alerts: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # An admin's changes to the account carry this (the person's own preferences don't)
    version: Mapped[int] = mapped_column(Integer, default=1)

    team: Mapped[Team | None] = relationship(back_populates="members", lazy="joined")
    backup_codes: Mapped[list[BackupCode]] = relationship(cascade="all, delete-orphan")


class BackupCode(Base):
    __tablename__ = "backup_codes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(255))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invitation(Base):
    """A one-time link to join ostraka, or, with `user_id`, to re-enrol a locked-out account.

    Accepting is two steps: name and password first (held here, hashed, with a fresh TOTP secret),
    then a TOTP code proving the authenticator works. Only then does the account exist.
    """

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), index=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "invitation_role"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Filled in by the first acceptance step
    pending_name: Mapped[str | None] = mapped_column(String(120))
    pending_password_hash: Mapped[str | None] = mapped_column(String(255))
    pending_totp_secret: Mapped[str | None] = mapped_column(Text)

    team: Mapped[Team | None] = relationship(lazy="joined")


class Session(Base):
    """A signed-in browser. The refresh token rotates on every use; replaying an old one ends the
    whole chain, because only a stolen copy would be presented twice."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))  # shown in lists, e.g. ost_3f9a2c
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scope: Mapped[TokenScope] = mapped_column(_enum(TokenScope, "token_scope"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityEvent(Base):
    """Sign-ins, invitations, role and MFA changes. Phase 2's activity log covers incident work."""

    __tablename__ = "security_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ip: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
