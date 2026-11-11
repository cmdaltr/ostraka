"""Request and response bodies for the people API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from ostraka import branding, profile
from ostraka.models import Availability, NotificationKind, Role, TokenScope

Palette = Literal["cobalt", "ostrakon", "ledger"]
Mode = Literal["system", "light", "dark"]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TeamOut(_Out):
    id: uuid.UUID
    name: str
    code: str | None = None
    aliases: list[str] = Field(default_factory=list)
    description: str
    has_channel: bool = False  # a Teams channel is connected; the URL itself is never sent back
    version: int = 1


TEAM_CODE = r"^[A-Z]{2,8}$"


def _aliases(v: list[str] | None) -> list[str] | None:
    if v is None:
        return None
    out: list[str] = []
    for alias in (a.strip() for a in v):
        if alias and alias.lower() not in {o.lower() for o in out}:
            out.append(alias[:40])
    return out[:10]


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    code: str | None = Field(default=None, pattern=TEAM_CODE, description="A short code, e.g. CTI")
    aliases: list[str] = Field(default_factory=list, description="Other names for the team, e.g. Intel.")
    description: str = Field(default="", max_length=500)

    @field_validator("aliases")
    @classmethod
    def _clean_aliases(cls, v: list[str] | None) -> list[str] | None:
        return _aliases(v)


class TeamPatch(BaseModel):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=80)
    code: str | None = Field(default=None, pattern=TEAM_CODE)
    aliases: list[str] | None = None
    description: str | None = Field(default=None, max_length=500)

    @field_validator("aliases")
    @classmethod
    def _clean_aliases(cls, v: list[str] | None) -> list[str] | None:
        return _aliases(v)


class UserOut(_Out):
    """What anyone signed in may see about a colleague: enough to mention, assign or ring them."""

    id: uuid.UUID
    name: str
    email: str
    role: Role
    team: TeamOut | None
    is_active: bool
    job_title: str = ""
    availability: Availability = Availability.AVAILABLE
    timezone: str = "Europe/London"
    phone: str | None = Field(default=None, description="Out of hours. Withheld from observers.")
    version: int = 1


class MeOut(UserOut):
    theme_palette: Palette
    theme_mode: Mode
    show_raw_iocs: bool
    notify_email: bool
    notify_push: bool
    muted_kinds: list[str]
    quiet_from: str | None
    quiet_until: str | None
    new_device_alerts: bool
    last_login_at: datetime | None
    backup_codes_left: int = 0


# Kinds a person may keep to the bell. A new-device alert has its own switch.
MUTABLE_KINDS = [k.value for k in NotificationKind if k is not NotificationKind.NEW_DEVICE]


class MePatch(BaseModel):
    """Your own profile and preferences. Send "" to clear the phone or quiet hours."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    timezone: str | None = Field(default=None, max_length=64)
    availability: Availability | None = None
    theme_palette: Palette | None = None
    theme_mode: Mode | None = None
    show_raw_iocs: bool | None = None
    notify_email: bool | None = None
    notify_push: bool | None = None
    muted_kinds: list[str] | None = None
    quiet_from: str | None = None
    quiet_until: str | None = None
    new_device_alerts: bool | None = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v and not profile.PHONE.match(v):
            raise ValueError("Use digits, spaces and an optional leading +, e.g. +44 7700 900123.")
        return v

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, v: str | None) -> str | None:
        if v is not None and not profile.valid_timezone(v):
            raise ValueError("Choose a time zone from the list, such as Europe/London.")
        return v

    @field_validator("quiet_from", "quiet_until")
    @classmethod
    def _hhmm(cls, v: str | None) -> str | None:
        if v is not None and v != "" and not profile.HHMM.match(v):
            raise ValueError("Use a 24-hour time, such as 22:00.")
        return v

    @field_validator("muted_kinds")
    @classmethod
    def _kinds(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        unknown = sorted(set(v) - set(MUTABLE_KINDS))
        if unknown:
            raise ValueError(f"Not a kind of notification you can mute: {', '.join(unknown)}.")
        return sorted(set(v))


class SessionOut(BaseModel):
    """A browser you're signed in on: one per sign-in, however often its token has rotated."""

    family: uuid.UUID
    device: str
    ip: str
    signed_in_at: datetime
    last_active_at: datetime
    expires_at: datetime
    current: bool


class SecurityEventOut(BaseModel):
    id: uuid.UUID
    at: datetime
    action: str
    ip: str
    by: str | None = Field(default=None, description="Who did it, when it wasn't you (an admin)")
    detail: dict


class AuthenticatorStart(BaseModel):
    """Proof it's you before a new authenticator replaces the old one."""

    password: str = Field(max_length=256)
    code: str = Field(min_length=6, max_length=7)


class AccountPatch(BaseModel):
    """An admin's change to someone else's account."""

    version: int
    role: Role | None = None
    team_id: uuid.UUID | None = None
    is_active: bool | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class MfaIn(BaseModel):
    ticket: str
    code: str = Field(min_length=6, max_length=16, description="A 6-digit TOTP code or a backup code")


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 (the OAuth token type, not a secret)
    expires_in: int


class LoginStep(BaseModel):
    mfa_required: Literal[True] = True
    ticket: str


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=16)


class BackupCodesOut(BaseModel):
    backup_codes: list[str]


class InvitationIn(BaseModel):
    email: EmailStr
    role: Role
    team_id: uuid.UUID | None = None

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class InvitationOut(_Out):
    id: uuid.UUID
    email: str
    role: Role
    team: TeamOut | None
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    is_reset: bool = False


class InvitationCreated(InvitationOut):
    link: str = Field(description="Shown once. Send it to the person; Ostraka only keeps a hash.")


class InvitationPreview(BaseModel):
    email: str
    role: Role
    team: str | None
    is_reset: bool
    expires_at: datetime


class AcceptStart(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(max_length=256)


class AcceptEnrol(BaseModel):
    otpauth_uri: str
    qr_svg: str
    secret: str = Field(description="For authenticators that can't scan the QR code")


class AcceptDone(TokenOut):
    backup_codes: list[str]


class ApiTokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scope: TokenScope = TokenScope.FULL
    expires_in_days: int | None = Field(default=90, ge=1, le=366)


class ApiTokenOut(_Out):
    id: uuid.UUID
    name: str
    prefix: str
    scope: TokenScope
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None


class ApiTokenCreated(ApiTokenOut):
    token: str = Field(description=f"Shown once. Starts with {branding.TOKEN_PREFIX}.")
