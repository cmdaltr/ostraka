"""Request and response bodies for incidents and what's recorded in them."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from ostraka.models import (
    AssetStatus,
    FindingKind,
    FindingStatus,
    IncidentRole,
    IncidentStatus,
    LinkKind,
    Phase,
    RecordKind,
    Severity,
    Tlp,
    Verdict,
    VerificationMethod,
)
from ostraka.observables.detect import ObservableType

TECHNIQUE = re.compile(r"^T\d{4}(?:\.\d{3})?$")
TAG = re.compile(r"^[a-z0-9][a-z0-9:._/-]{0,63}$")


def _tags(values: list[str]) -> list[str]:
    cleaned = []
    for value in values:
        tag = value.strip().lower().replace(" ", "-")
        if not TAG.match(tag):
            raise ValueError(f"“{value}” isn't a usable tag: letters, digits and : . _ / - only.")
        if tag not in cleaned:
            cleaned.append(tag)
    return cleaned


class Versioned(BaseModel):
    """Edits carry the version they were made against; a stale one is refused, not overwritten."""

    version: int


# ---- incidents ------------------------------------------------------------------------------------


class Person(BaseModel):
    id: uuid.UUID
    name: str


class TeamRef(BaseModel):
    id: uuid.UUID
    name: str


class MemberOut(BaseModel):
    user: Person
    role: IncidentRole


class IncidentIn(BaseModel):
    kind: RecordKind = RecordKind.INCIDENT
    title: str = Field(min_length=3, max_length=200)
    summary: str = Field(default="", max_length=20_000)
    severity: Severity
    tlp: Tlp = Tlp.AMBER
    team_ids: list[uuid.UUID] = Field(default_factory=list)
    aware_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    playbook_ids: list[uuid.UUID] = Field(default_factory=list, description="Playbooks to apply on declaring")

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        return _tags(v)


class IncidentPatch(Versioned):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    summary: str | None = Field(default=None, max_length=20_000)
    severity: Severity | None = None
    tlp: Tlp | None = None
    phase: Phase | None = None
    status: IncidentStatus | None = None
    aware_at: datetime | None = None
    tags: list[str] | None = None
    sensitive: bool | None = Field(default=None, description="Only the commander may export reports")
    brand_id: uuid.UUID | None = Field(default=None, description="Report brand (MSSP); null for the default")

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str] | None) -> list[str] | None:
        return _tags(v) if v is not None else None


class IncidentSummary(BaseModel):
    id: uuid.UUID
    kind: RecordKind
    ref: str
    title: str
    severity: Severity
    tlp: Tlp
    phase: Phase
    status: IncidentStatus
    commander: Person | None
    teams: list[TeamRef]
    declared_at: datetime
    updated_at: datetime
    tags: list[str]


class IncidentOut(IncidentSummary):
    summary: str
    aware_at: datetime
    closed_at: datetime | None
    members: list[MemberOut]
    version: int
    can_edit: bool
    can_manage: bool
    sensitive: bool
    can_export: bool
    brand_id: uuid.UUID | None
    counts: dict[str, int]


class LinkedRecord(BaseModel):
    id: uuid.UUID
    link_id: uuid.UUID
    kind: RecordKind
    ref: str
    title: str
    status: IncidentStatus
    phase: Phase
    severity: Severity


class LinksOut(BaseModel):
    """This record's place among others. Records you can't open are counted, not named."""

    parent: LinkedRecord | None = None
    children: list[LinkedRecord] = Field(default_factory=list)
    related: list[LinkedRecord] = Field(default_factory=list)
    duplicate_of: LinkedRecord | None = None
    duplicates: list[LinkedRecord] = Field(default_factory=list)
    promoted_from: LinkedRecord | None = None
    promoted_to: list[LinkedRecord] = Field(default_factory=list)
    hidden: int = 0


class LinkIn(BaseModel):
    """A link from this record, read as "this record is <relation> <ref>"."""

    ref: str = Field(min_length=5, max_length=32)
    relation: str = Field(pattern="^(child_of|parent_of|related_to|duplicate_of)$")


class PromoteIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    summary: str = Field(default="", max_length=20_000)
    severity: Severity
    tlp: Tlp | None = Field(default=None, description="The hunt's TLP when left out")


class MemberIn(BaseModel):
    role: IncidentRole = IncidentRole.RESPONDER


class TeamAdd(BaseModel):
    team_id: uuid.UUID


# ---- observables ----------------------------------------------------------------------------------


class SightingIn(BaseModel):
    seen_at: datetime | None = None
    asset_id: uuid.UUID | None = None
    host: str = Field(default="", max_length=255)
    user_account: str = Field(default="", max_length=255)
    source: str = Field(default="", max_length=255)


class SightingOut(SightingIn):
    id: uuid.UUID
    recorded_by: uuid.UUID | None
    recorded_at: datetime


class ObservableIn(BaseModel):
    value: str = Field(min_length=1, max_length=4000)
    kind: ObservableType | None = Field(default=None, description="Detected from the value when left out")
    verdict: Verdict = Verdict.UNKNOWN
    note: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list)
    sighting: SightingIn | None = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        return _tags(v)


class ObservablePatch(Versioned):
    verdict: Verdict | None = None
    note: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str] | None) -> list[str] | None:
        return _tags(v) if v is not None else None


class Elsewhere(BaseModel):
    """The same observable in other incidents: named where you can see them, counted where you can't."""

    visible: list[dict] = Field(default_factory=list)  # {id, ref, title}
    hidden: int = 0
    ask: list[str] = Field(default_factory=list)  # "DFIR (lead: Dana Lead)"


class IncidentObservableOut(BaseModel):
    id: uuid.UUID
    ref: str
    kind: ObservableType
    value: str | None  # withheld from observers who haven't asked to see raw IOCs
    masked: bool = False
    verdict: Verdict
    note: str
    tags: list[str]
    sightings: list[SightingOut]
    added_at: datetime
    version: int
    elsewhere: Elsewhere


class ExtractIn(BaseModel):
    text: str = Field(max_length=200_000)


class Extracted(BaseModel):
    kind: ObservableType
    value: str
    confidence: float
    already_in_incident: bool


# ---- assets ---------------------------------------------------------------------------------------


class AssetIn(BaseModel):
    hostname: str = Field(min_length=1, max_length=255)
    ip: str = Field(default="", max_length=64)
    public_ip: str = Field(default="", max_length=64)
    os: str = Field(default="", max_length=120)
    primary_user: str = Field(default="", max_length=255)
    purpose: str = Field(default="", max_length=5000)
    data_types: list[str] = Field(default_factory=list)
    criticality: Severity = Severity.MEDIUM
    status: AssetStatus = AssetStatus.IDENTIFIED
    is_patient_zero: bool = False
    tags: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=20_000)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        return _tags(v)


class AssetPatch(Versioned):
    hostname: str | None = Field(default=None, min_length=1, max_length=255)
    ip: str | None = Field(default=None, max_length=64)
    public_ip: str | None = Field(default=None, max_length=64)
    os: str | None = Field(default=None, max_length=120)
    primary_user: str | None = Field(default=None, max_length=255)
    purpose: str | None = Field(default=None, max_length=5000)
    data_types: list[str] | None = None
    criticality: Severity | None = None
    status: AssetStatus | None = None
    is_patient_zero: bool | None = None
    tags: list[str] | None = None
    notes: str | None = Field(default=None, max_length=20_000)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str] | None) -> list[str] | None:
        return _tags(v) if v is not None else None


class AssetOut(AssetIn):
    id: uuid.UUID
    ref: str
    created_at: datetime
    version: int


class AssetLinkIn(BaseModel):
    source_id: uuid.UUID
    target_id: uuid.UUID
    kind: LinkKind
    seen_at: datetime | None = None
    technique: str = Field(default="", max_length=16)
    note: str = Field(default="", max_length=5000)

    @field_validator("technique")
    @classmethod
    def _technique(cls, v: str) -> str:
        v = v.strip().upper()
        if v and not TECHNIQUE.match(v):
            raise ValueError("Use an ATT&CK technique ID such as T1021.001.")
        return v

    @model_validator(mode="after")
    def _distinct(self) -> AssetLinkIn:
        if self.source_id == self.target_id:
            raise ValueError("A link joins two different assets.")
        return self


class AssetLinkOut(AssetLinkIn):
    id: uuid.UUID
    created_at: datetime


# ---- findings -------------------------------------------------------------------------------------


class FindingIn(BaseModel):
    kind: FindingKind
    title: str = Field(min_length=3, max_length=200)
    statement: str = Field(default="", max_length=20_000)
    occurred_at: datetime | None = None
    asset_id: uuid.UUID | None = None
    techniques: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    observable_ids: list[uuid.UUID] = Field(default_factory=list, description="Incident observable ids")
    source_reliability: str | None = Field(default=None, pattern="^[A-F]$")
    information_credibility: str | None = Field(default=None, pattern="^[1-6]$")
    handling_code: str | None = Field(default=None, pattern="^[PC]$")
    handling_conditions: str = Field(default="", max_length=2000)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        return _tags(v)

    @field_validator("techniques")
    @classmethod
    def _techniques(cls, v: list[str]) -> list[str]:
        out = []
        for t in v:
            t = t.strip().upper()
            if not TECHNIQUE.match(t):
                raise ValueError(f"“{t}” isn't an ATT&CK technique ID such as T1059.001.")
            if t not in out:
                out.append(t)
        return out

    @model_validator(mode="after")
    def _handling(self) -> FindingIn:
        if self.handling_code == "C" and not self.handling_conditions.strip():
            raise ValueError("Handling code C needs its conditions written down.")
        return self


class FindingPatch(Versioned):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    statement: str | None = Field(default=None, max_length=20_000)
    occurred_at: datetime | None = None
    asset_id: uuid.UUID | None = None
    techniques: list[str] | None = None
    tags: list[str] | None = None
    observable_ids: list[uuid.UUID] | None = None
    source_reliability: str | None = Field(default=None, pattern="^[A-F]$")
    information_credibility: str | None = Field(default=None, pattern="^[1-6]$")
    handling_code: str | None = Field(default=None, pattern="^[PC]$")
    handling_conditions: str | None = Field(default=None, max_length=2000)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str] | None) -> list[str] | None:
        return _tags(v) if v is not None else None

    @field_validator("techniques")
    @classmethod
    def _techniques(cls, v: list[str] | None) -> list[str] | None:
        return FindingIn._techniques(v) if v is not None else None


class VerificationIn(BaseModel):
    method: VerificationMethod
    platform: str = Field(default="", max_length=120)
    query: str = Field(default="", max_length=20_000)
    scope: str = Field(min_length=1, max_length=500, description="The host or source searched")
    time_from: datetime | None = None
    time_to: datetime | None = None
    result_count: int | None = Field(default=None, ge=0)
    reference: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def _complete(self) -> VerificationIn:
        if self.method is VerificationMethod.SEARCH and not (self.platform.strip() and self.query.strip()):
            raise ValueError("A search verification needs the platform and the query that was run.")
        if self.method is VerificationMethod.ROWANHOLT and not self.reference.strip():
            raise ValueError("A Rowanholt verification needs the job, host, artefact and row it cites.")
        if self.time_from and self.time_to and self.time_from > self.time_to:
            raise ValueError("The search window ends before it starts.")
        return self


class VerificationOut(VerificationIn):
    id: uuid.UUID
    ran_by: Person | None
    ran_at: datetime


class DecisionIn(Versioned):
    note: str = Field(default="", max_length=5000)


class FindingOut(BaseModel):
    id: uuid.UUID
    ref: str
    kind: FindingKind
    title: str
    statement: str
    status: FindingStatus
    occurred_at: datetime | None
    asset_id: uuid.UUID | None
    techniques: list[str]
    tags: list[str]
    observable_ids: list[uuid.UUID]
    source_reliability: str | None
    information_credibility: str | None
    handling_code: str | None
    handling_conditions: str
    created_by: Person | None
    created_at: datetime
    decided_by: Person | None
    decided_at: datetime | None
    decision_note: str
    verifications: list[VerificationOut]
    version: int


# ---- comments, activity, timeline -----------------------------------------------------------------

TARGETS = ("incident", "finding", "asset", "observable", "task")


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    target_type: str = Field(default="incident", pattern=f"^({'|'.join(TARGETS)})$")
    target_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None


class CommentPatch(Versioned):
    body: str = Field(min_length=1, max_length=20_000)


class MaybeVersioned(BaseModel):
    """A state change with no other fields. Ostraka's own clients always send the version; a stale one
    is refused."""

    version: int | None = None


class CommentOut(BaseModel):
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID | None
    parent_id: uuid.UUID | None
    author: Person | None
    body: str
    created_at: datetime
    edited_at: datetime | None
    deleted: bool
    resolved_at: datetime | None
    version: int


class ActivityOut(BaseModel):
    position: int
    at: datetime
    actor: Person | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    summary: str
    data: dict


class ChainOut(BaseModel):
    ok: bool
    events: int
    broken_at: int | None


class TimelineItem(BaseModel):
    """One thing that happened in the attack, from whichever record says so."""

    at: datetime
    recorded_at: datetime
    kind: str  # sighting | finding | link
    ref: str
    title: str
    detail: str
    entity_id: uuid.UUID
    asset: str | None
    techniques: list[str]
