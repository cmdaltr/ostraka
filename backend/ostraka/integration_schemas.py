"""Request and response shapes for the suite links: Rowanholt and Shikari servers, Rowanholt links
and citations, and detection gaps."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from ostraka.incident_schemas import Person
from ostraka.models import GapStatus, Product, Severity, TaskStatus, Tlp

# ---- the installation's servers ---------------------------------------------------------------------


class IntegrationIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    token: str | None = Field(default=None, max_length=500, description="Left out: keep the stored one")
    detection_team_id: uuid.UUID | None = None
    enabled: bool = True
    version: int | None = Field(default=None, description="Needed when changing a server already saved")


class IntegrationOut(BaseModel):
    product: Product
    url: str
    has_token: bool
    has_webhook_secret: bool
    detection_team_id: uuid.UUID | None
    enabled: bool
    checked_at: datetime | None
    check_ok: bool | None
    check_message: str
    updated_by: Person | None
    updated_at: datetime
    version: int
    webhook_secret: str | None = None  # Shikari: only when it's made or replaced


class IntegrationsOut(BaseModel):
    rowanholt: IntegrationOut | None
    shikari: IntegrationOut | None
    shikari_webhook_url: str  # where Shikari's webhook should point
    automation: bool  # whether Shikari's webhooks are acted on (the `automation` feature)


class CheckStep(BaseModel):
    name: str
    ok: bool
    detail: str


class ConnectionTest(BaseModel):
    ok: bool
    message: str
    steps: list[CheckStep]


# ---- Rowanholt on an incident -----------------------------------------------------------------------


class RowanholtLinkIn(BaseModel):
    job_id: str = Field(default="", max_length=64)
    case_number: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _one(self) -> RowanholtLinkIn:
        self.job_id, self.case_number = self.job_id.strip(), self.case_number.strip()
        if bool(self.job_id) == bool(self.case_number):
            raise ValueError("Give a Rowanholt job id or a case number, not both.")
        return self


class RemoteJob(BaseModel):
    id: str
    status: str
    created_at: str | None = None
    url: str


class RowanholtLinkOut(BaseModel):
    id: uuid.UUID
    job_id: str
    case_number: str
    status: str
    progress: int | None
    jobs: list[RemoteJob]
    checked_at: datetime | None
    url: str
    linked_by: Person | None
    linked_at: datetime


class CitationOut(BaseModel):
    id: uuid.UUID
    finding_id: uuid.UUID
    finding_ref: str
    job_id: str
    case_number: str
    host: str
    artefact: str
    row_ref: str
    row_hash: str
    cite: str
    excerpt: dict[str, str]
    remote_finding_id: str | None
    remote_status: str
    verification_id: uuid.UUID | None
    url: str
    cited_by: Person | None
    cited_at: datetime


class RowanholtOut(BaseModel):
    configured: bool
    reachable: bool | None  # None: not asked (not configured, or the TLP guard)
    message: str
    links: list[RowanholtLinkOut]
    citations: list[CitationOut]


class RemoteFinding(BaseModel):
    id: str
    job_id: str
    host: str
    artefact: str
    row_ref: str
    row_hash: str
    title: str
    technique: str
    status: str  # Rowanholt's: lead, confirmed or benign
    intact: bool | None
    excerpt: str
    imported_as: str | None  # FND-12, when it's already in this incident
    cite: str


class ImportIn(BaseModel):
    job_id: str = Field(min_length=1, max_length=64)
    finding_ids: list[str] = Field(min_length=1, max_length=200)


class ImportOut(BaseModel):
    imported: list[str]  # the new leads' references
    skipped: int  # already imported


class CitationIn(BaseModel):
    link_id: uuid.UUID
    job_id: str = Field(default="", max_length=64, description="For a case link: which of its jobs")
    host: str = Field(min_length=1, max_length=255)
    artefact: str = Field(min_length=1, max_length=1000)
    row_ref: str = Field(min_length=1, max_length=32)
    verify: bool = Field(default=False, description="Also attach it as the finding's verification")
    note: str = Field(default="", max_length=5000)


class RowsOut(BaseModel):
    columns: list[str]
    rows: list[dict]
    total: int
    exact: bool


# ---- detection gaps ---------------------------------------------------------------------------------


class GapTask(BaseModel):
    id: uuid.UUID
    ref: str
    title: str
    status: TaskStatus


class GapIncident(BaseModel):
    id: uuid.UUID
    ref: str
    title: str
    severity: Severity
    tlp: Tlp


class GapOut(BaseModel):
    id: uuid.UUID
    technique: str
    technique_name: str
    attack_url: str
    status: GapStatus
    finding_id: uuid.UUID | None
    finding_ref: str | None
    task: GapTask | None
    opened_by: Person | None
    opened_at: datetime
    checked_at: datetime | None
    check_error: str
    closed_at: datetime | None
    closed_by: str
    incident: GapIncident | None = None  # in the list across incidents


class GapsOut(BaseModel):
    configured: bool
    automation: bool
    coverage_url: str | None
    message: str = ""
    gaps: list[GapOut]


class GapTaskIn(BaseModel):
    team_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _owner(self) -> GapTaskIn:
        if self.team_id is None and self.assignee_id is None:
            raise ValueError("Give the task to a team or a person.")
        return self
