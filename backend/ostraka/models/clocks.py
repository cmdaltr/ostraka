"""Regulator clocks: a deadline regime on an incident, with an owner, and a row per milestone that
records when it was met and how far its nudges have gone. Deadlines aren't stored: they're worked
out from the regime's rules (`ostraka.clocks.regimes`) each time, so a corrected awareness or
classification time moves them."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ostraka.db import Base
from ostraka.models.people import _enum, utcnow


class RegimeKey(StrEnum):
    UK_GDPR = "uk_gdpr"
    UK_NIS = "uk_nis"
    EU_NIS2 = "eu_nis2"
    DORA = "dora"
    CUSTOM = "custom"


class Clock(Base):
    __tablename__ = "clocks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # CLK-3
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    regime: Mapped[RegimeKey] = mapped_column(_enum(RegimeKey, "clock_regime"))
    # What it's for: "Beazley cyber policy", "Board", or a note on a built-in regime
    title: Mapped[str] = mapped_column(String(200), default="")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # When this clock's awareness counts from, if not the incident's own aware_at
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the incident was classified under this regime (DORA: as major)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/London")
    # A custom clock's milestones: [{"name", "amount", "unit", "basis"}], keyed m1, m2… by position
    milestones: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    close_reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    marks: Mapped[list[ClockMilestone]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ClockMilestone.key"
    )

    @property
    def ref(self) -> str:
        return f"CLK-{self.seq}"


class ClockMilestone(Base):
    """One milestone's record: who met it, when, and under what reference; and the highest nudge
    level already sent, so each one goes once."""

    __tablename__ = "clock_milestones"

    clock_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clocks.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    met_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    met_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reference: Mapped[str] = mapped_column(String(200), default="")  # the regulator's case number
    note: Mapped[str] = mapped_column(Text, default="")
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nudged: Mapped[int] = mapped_column(Integer, default=0)
    nudged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
