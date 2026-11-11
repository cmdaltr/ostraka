"""Request and response bodies for regulator clocks."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from ostraka import profile
from ostraka.clocks.regimes import Basis, State, Unit
from ostraka.incident_schemas import Person, Versioned
from ostraka.models import RegimeKey, Severity, Tlp


class CustomMilestone(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount: int = Field(ge=1)
    unit: Unit
    basis: Basis = Basis.AWARE


def _zone(v: str | None) -> str | None:
    if v is not None and not profile.valid_timezone(v):
        raise ValueError("That isn't a time zone Ostraka knows, such as Europe/London.")
    return v


class ClockIn(BaseModel):
    regime: RegimeKey
    title: str = Field(default="", max_length=200)
    owner_id: uuid.UUID | None = Field(default=None, description="Defaults to whoever adds the clock")
    starts_at: datetime | None = Field(
        default=None, description="Count from this, not the incident's aware_at"
    )
    classified_at: datetime | None = None
    timezone: str | None = Field(default=None, description="For calendar spans; defaults to the regime's")
    milestones: list[CustomMilestone] = Field(default_factory=list, max_length=10)

    _tz = field_validator("timezone")(_zone)


class ClockPatch(Versioned):
    title: str | None = Field(default=None, max_length=200)
    owner_id: uuid.UUID | None = None
    starts_at: datetime | None = None
    classified_at: datetime | None = None
    timezone: str | None = None
    milestones: list[CustomMilestone] | None = Field(default=None, max_length=10)

    _tz = field_validator("timezone")(_zone)


class ClockClose(Versioned):
    reason: str = Field(min_length=3, max_length=2000, description="Why the clock stops: 'Not reportable: …'")


class MilestoneMet(Versioned):
    reference: str = Field(default="", max_length=200, description="The regulator's case number, say")
    note: str = Field(default="", max_length=5000)
    met_at: datetime | None = Field(default=None, description="When it was submitted; defaults to now")


class MilestoneOut(BaseModel):
    key: str
    name: str
    due_at: datetime | None
    starts_at: datetime | None
    provisional: bool
    counted: str
    state: State
    met_at: datetime | None
    met_by: Person | None
    reference: str
    note: str


class ClockOut(BaseModel):
    id: uuid.UUID
    ref: str
    regime: RegimeKey
    regime_name: str
    authority: str
    title: str
    owner: Person | None
    aware_at: datetime  # what awareness counts from: the clock's own start or the incident's
    starts_at: datetime | None
    classified_at: datetime | None
    needs_classification: bool
    timezone: str
    custom_milestones: list[CustomMilestone]
    milestones: list[MilestoneOut]
    closed_at: datetime | None
    closed_by: Person | None
    close_reason: str
    created_by: Person | None
    created_at: datetime
    version: int
    can_manage: bool
    can_mark: bool


class RegimeMilestoneOut(BaseModel):
    key: str
    name: str
    counted: str


class RegimeOut(BaseModel):
    key: RegimeKey
    name: str
    authority: str
    timezone: str
    source: str
    custom: bool
    needs_classification: bool
    milestones: list[RegimeMilestoneOut]


class ClockIncident(BaseModel):
    id: uuid.UUID
    ref: str
    title: str
    severity: Severity
    tlp: Tlp


class MyDeadline(BaseModel):
    """The next unmet milestone on a clock you own or whose incident you command."""

    clock_id: uuid.UUID
    clock_ref: str
    regime_name: str
    title: str
    key: str
    name: str
    due_at: datetime
    starts_at: datetime
    provisional: bool
    state: State
    owner: Person | None
    as_owner: bool
    incident: ClockIncident
