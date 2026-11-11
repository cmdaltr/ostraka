"""Stakeholder updates: the rhythm an incident's updates follow, who receives them, and each update
with its revisions.

An update is drafted by Ostraka from what changed since the last one, revised while it's a draft, and
frozen once comms approves and sends it. Every revision of a draft is kept, so the record shows what
was changed before it went out.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ostraka.db import Base
from ostraka.models.people import Team, User, _enum, utcnow


class UpdateStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"  # approved by comms and sent; never changed again
    DISCARDED = "discarded"


class UpdatePlan(Base):
    """An incident's update rhythm. Without a row, or with `rhythm_set` false, the severity's rhythm
    applies; with it set, `every_minutes` is the commander's or comms' choice (None: on change only)."""

    __tablename__ = "update_plans"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    rhythm_set: Mapped[bool] = mapped_column(Boolean, default=False)
    every_minutes: Mapped[int | None] = mapped_column(Integer)
    # The due time the scheduler last drafted or nudged for, so each falls due once
    nudged_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    set_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


class Stakeholder(Base):
    """Someone an incident's updates go to: a person in Ostraka, an email address outside it, or a
    team's Microsoft Teams channel."""

    __tablename__ = "stakeholders"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL)::int + (email IS NOT NULL)::int + (team_id IS NOT NULL)::int = 1",
            name="one_kind",
        ),
        UniqueConstraint("incident_id", "user_id", name="uq_stakeholders_user"),
        UniqueConstraint("incident_id", "email", name="uq_stakeholders_email"),
        UniqueConstraint("incident_id", "team_id", name="uq_stakeholders_team"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    email: Mapped[str | None] = mapped_column(String(320))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120), default="")  # how an outside address is shown
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User | None] = relationship(lazy="joined", foreign_keys=[user_id])
    team: Mapped[Team | None] = relationship(lazy="joined")


class StakeholderUpdate(Base):
    """One update, from draft to sent. `content` is the structured draft Ostraka composed;
    `rephrased` an AI rewording of its lines that passed the rephrase check, used only if chosen."""

    __tablename__ = "stakeholder_updates"
    __table_args__ = (
        UniqueConstraint("incident_id", "number", name="uq_stakeholder_updates_number"),
        # One draft at a time per incident
        Index(
            "uq_stakeholder_updates_one_draft",
            "incident_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # UPD-7
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)  # the incident's first, second, third update
    status: Mapped[UpdateStatus] = mapped_column(
        _enum(UpdateStatus, "update_status"), default=UpdateStatus.DRAFT
    )
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # what it covers: since..until
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assessments.id", ondelete="SET NULL"))
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")  # comms' own words, linted like the rest
    rephrased: Mapped[list | None] = mapped_column(JSON)  # the lines, reworded
    rephrased_by: Mapped[str] = mapped_column(String(40), default="")  # provider: "ollama", "anthropic"
    use_rephrased: Mapped[bool] = mapped_column(Boolean, default=False)
    drafted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    drafted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recipients: Mapped[list] = mapped_column(JSON, default=list)  # who it was sent to, as sent
    version: Mapped[int] = mapped_column(Integer, default=1)

    revisions: Mapped[list[UpdateRevision]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="UpdateRevision.version"
    )

    @property
    def ref(self) -> str:
        return f"UPD-{self.seq}"


class UpdateRevision(Base):
    """A draft as it stood at one version: what changed, by whom, and the text at that point."""

    __tablename__ = "update_revisions"

    update_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stakeholder_updates.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    change: Mapped[str] = mapped_column(String(40))  # drafted, rebuilt, note, rephrased, original, sent
    lines: Mapped[list] = mapped_column(JSON, default=list)
    by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
