"""The analytic record of an incident: assessments written to the reporting standard, Analysis of
Competing Hypotheses (ACH), and lessons learned.

An assessment is never edited. A new one supersedes it, so the report can show how the judgement
moved as the evidence came in.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ostraka.db import Base
from ostraka.models.people import _enum, utcnow


class Probability(StrEnum):
    """The PHIA probability yardstick: the only words an assessment may use for likelihood."""

    REMOTE_CHANCE = "remote_chance"  # ≤5%
    HIGHLY_UNLIKELY = "highly_unlikely"  # 10–20%
    UNLIKELY = "unlikely"  # 25–35%
    REALISTIC_POSSIBILITY = "realistic_possibility"  # 40–<50%
    LIKELY = "likely"  # 55–75%
    HIGHLY_LIKELY = "highly_likely"  # 80–90%
    ALMOST_CERTAIN = "almost_certain"  # ≥95%


class AssessmentStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"


class Assessment(Base):
    """A judgement in structured parts: what we assess, how likely, on what basis, what we looked for
    and didn't find, and what it means. ostraka writes the sentences from the parts."""

    __tablename__ = "assessments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # ASM-4
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    judgement: Mapped[str] = mapped_column(Text)  # "the actor copied the payroll export off the network"
    probability: Mapped[Probability] = mapped_column(_enum(Probability, "probability"))
    so_what: Mapped[str] = mapped_column(Text, default="")
    what_next: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[AssessmentStatus] = mapped_column(
        _enum(AssessmentStatus, "assessment_status"), default=AssessmentStatus.CURRENT
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assessments.id", ondelete="SET NULL"))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    basis: Mapped[list[AssessmentBasis]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AssessmentBasis.position"
    )
    absences: Mapped[list[NegativeEvidence]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="NegativeEvidence.position"
    )


class AssessmentBasis(Base):
    """A finding an assessment rests on, in the order its author gave."""

    __tablename__ = "assessment_basis"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), primary_key=True
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)


class NegativeEvidence(Base):
    """Negative evidence ("there is no evidence of X") with the search that came back empty, so the
    reader knows where nobody looked as well as where they did."""

    __tablename__ = "negative_evidence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    absent: Mapped[str] = mapped_column(String(500))  # "data leaving the network"
    platform: Mapped[str] = mapped_column(String(120))
    query: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(500))
    time_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---- Analysis of Competing Hypotheses --------------------------------------------------------------


class HypothesisStatus(StrEnum):
    OPEN = "open"
    REJECTED = "rejected"


class Weight(StrEnum):
    """Credibility and relevance of a piece of evidence."""

    HIGH = "h"
    MEDIUM = "m"
    LOW = "l"


class Rating(StrEnum):
    """How a piece of evidence sits with a hypothesis."""

    CONSISTENT = "c"
    INCONSISTENT = "i"
    NOT_APPLICABLE = "na"


class EvidenceKind(StrEnum):
    FINDING = "finding"
    OBSERVABLE = "observable"
    ASSUMPTION = "assumption"
    ABSENCE = "absence"  # something we'd expect to see under a hypothesis, and don't


class AchHypothesis(Base):
    __tablename__ = "ach_hypotheses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # HYP-3
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    statement: Mapped[str] = mapped_column(Text)
    status: Mapped[HypothesisStatus] = mapped_column(
        _enum(HypothesisStatus, "hypothesis_status"), default=HypothesisStatus.OPEN
    )
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What would change our view of this hypothesis; it can be made a task
    milestone: Mapped[str] = mapped_column(Text, default="")
    milestone_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)


class AchEvidence(Base):
    __tablename__ = "ach_evidence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # EVD-9
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[EvidenceKind] = mapped_column(_enum(EvidenceKind, "evidence_kind"))
    finding_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("findings.id", ondelete="SET NULL"))
    incident_observable_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incident_observables.id", ondelete="SET NULL")
    )
    text: Mapped[str] = mapped_column(Text)
    credibility: Mapped[Weight] = mapped_column(_enum(Weight, "weight"), default=Weight.MEDIUM)
    relevance: Mapped[Weight] = mapped_column(_enum(Weight, "weight"), default=Weight.MEDIUM)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)


class AchRating(Base):
    """One analyst's rating of one piece of evidence against one hypothesis."""

    __tablename__ = "ach_ratings"

    hypothesis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ach_hypotheses.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ach_evidence.id", ondelete="CASCADE"), primary_key=True
    )
    rater_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    value: Mapped[Rating] = mapped_column(_enum(Rating, "rating"))
    rated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ---- Lessons learned --------------------------------------------------------------------------------


class LessonKind(StrEnum):
    WENT_WELL = "went_well"
    IMPROVE = "improve"


class LessonArea(StrEnum):
    PEOPLE = "people"
    PROCESS = "process"
    TECHNOLOGY = "technology"
    DETECTION = "detection"
    COMMUNICATION = "communication"


class Lesson(Base):
    """What the response taught: what happened, and what we'd keep or change. An improvement can
    become a follow-up task, so it doesn't stay a good intention."""

    __tablename__ = "lessons"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # LSN-2
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[LessonKind] = mapped_column(_enum(LessonKind, "lesson_kind"))
    area: Mapped[LessonArea] = mapped_column(_enum(LessonArea, "lesson_area"))
    observation: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text, default="")
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)
