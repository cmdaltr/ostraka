"""Request and response bodies for stakeholder updates and the observers' briefing."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

from ostraka.analysis_schemas import LintFlag
from ostraka.incident_schemas import Person, Versioned
from ostraka.models import Phase, Severity, Tlp, UpdateStatus
from ostraka.updates import rhythm


class RhythmRow(BaseModel):
    severity: Severity
    every_minutes: int | None
    words: str


class PlanOut(BaseModel):
    mode: Literal["severity", "every", "on_change"]
    every_minutes: int | None  # in force: the severity's, or the incident's own
    words: str
    by_severity: list[RhythmRow]
    last_sent_at: datetime | None
    next_due_at: datetime | None
    set_by: Person | None
    version: int


class PlanIn(Versioned):
    mode: Literal["severity", "every", "on_change"]
    every_minutes: int | None = Field(default=None, ge=rhythm.SHORTEST, le=rhythm.LONGEST)

    @model_validator(mode="after")
    def _minutes(self) -> PlanIn:
        if self.mode == "every" and self.every_minutes is None:
            raise ValueError("Say how often, in minutes.")
        return self


class StakeholderIn(BaseModel):
    """One of: a person in Ostraka, an outside email address, or a team's Teams channel."""

    user_id: uuid.UUID | None = None
    email: EmailStr | None = None
    team_id: uuid.UUID | None = None
    name: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def _one(self) -> StakeholderIn:
        if sum(x is not None for x in (self.user_id, self.email, self.team_id)) != 1:
            raise ValueError("Choose a person, an email address or a team's channel.")
        return self


class StakeholderOut(BaseModel):
    id: uuid.UUID
    kind: Literal["person", "email", "teams"]
    name: str
    email: str | None
    can_receive: bool  # a person who can see the incident, a configured channel
    why_not: str = ""


class LineOut(BaseModel):
    section: str
    kind: str
    ref: str
    text: str  # Ostraka's own words, masked for the viewer
    rephrased: str | None = None


class RevisionOut(BaseModel):
    version: int
    change: str
    by: Person | None
    at: datetime
    lines: list[str]


class UpdateOut(BaseModel):
    id: uuid.UUID
    ref: str
    number: int
    status: UpdateStatus
    since: datetime
    until: datetime
    assessment: str | None
    lines: list[LineOut]
    note: str
    rephrased_by: str
    use_rephrased: bool
    flags: list[LintFlag]
    can_approve: bool
    why_not: str = ""
    drafted_by: Person | None  # None: Ostraka, on the rhythm
    drafted_at: datetime
    approved_by: Person | None
    approved_at: datetime | None
    recipients: list[dict]
    revisions: list[RevisionOut]
    version: int


class UpdatesOut(BaseModel):
    plan: PlanOut
    stakeholders: list[StakeholderOut]
    draft: UpdateOut | None
    sent: list[UpdateOut]
    can_run: bool
    can_approve: bool
    why_not: str = ""
    email_enabled: bool
    ai: str  # the provider a rephrase would use, or "" when none may
    ai_why_not: str = ""
    restricted: bool  # TLP:RED or AMBER+STRICT: outside channels carry the reference only


class DraftPatch(Versioned):
    note: str | None = Field(default=None, max_length=3000)
    use_rephrased: bool | None = None


class RephraseOut(BaseModel):
    accepted: bool
    problems: list[str]
    update: UpdateOut


class BriefingUpdate(BaseModel):
    ref: str
    number: int
    approved_at: datetime | None
    approved_by: str
    lines: list[LineOut]
    ai_worded: bool


class BriefingOut(BaseModel):
    """The read-only briefing: where things stand, and every update sent, in order."""

    ref: str
    title: str
    severity: Severity
    tlp: Tlp
    phase: Phase
    status: str
    commander: str
    declared_at: datetime
    confirmed: int
    leads: int
    assets: int
    contained: int
    tasks_open: int
    tasks_done: int
    assessment: list[str]
    rhythm: str
    next_due_at: datetime | None
    updates: list[BriefingUpdate]
