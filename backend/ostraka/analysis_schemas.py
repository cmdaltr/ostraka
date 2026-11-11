"""Request and response bodies for assessments, ACH and lessons learned."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ostraka.incident_schemas import Person, Versioned
from ostraka.models import (
    AssessmentStatus,
    EvidenceKind,
    FindingStatus,
    HypothesisStatus,
    LessonArea,
    LessonKind,
    Probability,
    Rating,
    TaskStatus,
    Weight,
)

# ---- assessments ----------------------------------------------------------------------------------


class NegativeEvidenceIn(BaseModel):
    """Something looked for and not found, with the search that looked."""

    absent: str = Field(
        min_length=3, max_length=500, description="What wasn't found: 'data leaving the network'"
    )
    platform: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=20_000)
    scope: str = Field(
        min_length=1, max_length=500, description="The host or source searched, and nothing wider"
    )
    time_from: datetime | None = None
    time_to: datetime | None = None

    @model_validator(mode="after")
    def _window(self) -> NegativeEvidenceIn:
        if self.time_from and self.time_to and self.time_from > self.time_to:
            raise ValueError("The search window ends before it starts.")
        return self


class NegativeEvidenceOut(NegativeEvidenceIn):
    id: uuid.UUID


class AssessmentIn(BaseModel):
    judgement: str = Field(
        min_length=3, max_length=2000, description="The claim only; Ostraka adds 'We assess'"
    )
    probability: Probability
    finding_ids: list[uuid.UUID] = Field(default_factory=list, max_length=30)
    absences: list[NegativeEvidenceIn] = Field(default_factory=list, max_length=15)
    so_what: str = Field(default="", max_length=5000)
    what_next: str = Field(default="", max_length=5000)
    supersedes_id: uuid.UUID | None = Field(default=None, description="The assessment this one replaces")


class LintFlag(BaseModel):
    rule: str
    severity: Literal["error", "warning"]
    field: str
    message: str
    words: list[str]


class LintOut(BaseModel):
    """What ostraka would write from these parts, and what's wrong with them."""

    flags: list[LintFlag]
    sentences: list[str]


class BasisOut(BaseModel):
    finding_id: uuid.UUID
    ref: str
    title: str
    status: FindingStatus


class AssessmentOut(BaseModel):
    id: uuid.UUID
    ref: str
    judgement: str
    probability: Probability
    probability_words: str
    probability_range: str
    so_what: str
    what_next: str
    status: AssessmentStatus
    supersedes: str | None
    superseded_at: datetime | None
    basis: list[BasisOut]
    absences: list[NegativeEvidenceOut]
    sentences: list[str]
    flags: list[LintFlag]
    created_by: Person | None
    created_at: datetime


# ---- ACH ------------------------------------------------------------------------------------------


class HypothesisIn(BaseModel):
    statement: str = Field(min_length=3, max_length=2000)
    milestone: str = Field(default="", max_length=2000, description="What would change our view of it")


class HypothesisPatch(Versioned):
    statement: str | None = Field(default=None, min_length=3, max_length=2000)
    milestone: str | None = Field(default=None, max_length=2000)


class RejectIn(Versioned):
    reason: str = Field(min_length=3, max_length=5000)


class EvidenceIn(BaseModel):
    kind: EvidenceKind
    finding_id: uuid.UUID | None = None
    observable_id: uuid.UUID | None = Field(default=None, description="An incident observable id")
    text: str = Field(default="", max_length=2000)
    credibility: Weight = Weight.MEDIUM
    relevance: Weight = Weight.MEDIUM

    @model_validator(mode="after")
    def _source(self) -> EvidenceIn:
        if self.kind is EvidenceKind.FINDING and self.finding_id is None:
            raise ValueError("Choose the finding this evidence is.")
        if self.kind is EvidenceKind.OBSERVABLE and self.observable_id is None:
            raise ValueError("Choose the observable this evidence is.")
        if self.kind in (EvidenceKind.ASSUMPTION, EvidenceKind.ABSENCE) and len(self.text.strip()) < 3:
            raise ValueError("Say what the assumption is, or what's absent.")
        return self


class EvidencePatch(Versioned):
    text: str | None = Field(default=None, min_length=3, max_length=2000)
    credibility: Weight | None = None
    relevance: Weight | None = None


class RatingIn(BaseModel):
    hypothesis_id: uuid.UUID
    evidence_id: uuid.UUID
    value: Rating | None = Field(description="null clears your rating")


class FollowUpIn(BaseModel):
    """The task an ACH milestone or a lesson becomes."""

    title: str | None = Field(default=None, min_length=3, max_length=200)
    team_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None

    @model_validator(mode="after")
    def _owner(self) -> FollowUpIn:
        if self.team_id is None and self.assignee_id is None:
            raise ValueError("Give the task to a team or a person.")
        return self


class TaskRef(BaseModel):
    id: uuid.UUID
    ref: str
    status: TaskStatus


class HypothesisOut(BaseModel):
    id: uuid.UUID
    ref: str
    statement: str
    status: HypothesisStatus
    rejection_reason: str
    rejected_by: Person | None
    rejected_at: datetime | None
    milestone: str
    milestone_task: TaskRef | None
    score: float
    leading: bool
    created_by: Person | None
    created_at: datetime
    version: int


class EvidenceOut(BaseModel):
    id: uuid.UUID
    ref: str
    kind: EvidenceKind
    finding_id: uuid.UUID | None
    finding_ref: str | None
    observable_id: uuid.UUID | None
    text: str
    credibility: Weight
    relevance: Weight
    non_diagnostic: bool
    pivotal: bool
    lead_without: str | None = Field(
        description="Who'd lead if this evidence were wrong (a ref, or null: a tie)"
    )
    created_by: Person | None
    version: int


class RaterRating(BaseModel):
    rater: Person
    value: Rating


class CellOut(BaseModel):
    hypothesis_id: uuid.UUID
    evidence_id: uuid.UUID
    consensus: Rating | None
    disputed: bool
    mine: Rating | None
    ratings: list[RaterRating]


class AchOut(BaseModel):
    hypotheses: list[HypothesisOut]
    evidence: list[EvidenceOut]
    cells: list[CellOut]
    raters: list[Person]
    max_raters: int
    leading: str | None
    algorithm: str


# ---- lessons learned ------------------------------------------------------------------------------


class LessonIn(BaseModel):
    kind: LessonKind
    area: LessonArea
    observation: str = Field(min_length=3, max_length=5000, description="What happened")
    recommendation: str = Field(default="", max_length=5000, description="What we'd keep or change")


class LessonPatch(Versioned):
    kind: LessonKind | None = None
    area: LessonArea | None = None
    observation: str | None = Field(default=None, min_length=3, max_length=5000)
    recommendation: str | None = Field(default=None, max_length=5000)


class LessonOut(BaseModel):
    id: uuid.UUID
    ref: str
    kind: LessonKind
    area: LessonArea
    observation: str
    recommendation: str
    task: TaskRef | None
    created_by: Person | None
    created_at: datetime
    version: int
