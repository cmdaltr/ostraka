"""Handover: where each person last left an incident, and the shift-handover notes posted to it.

A view is a bookmark in the activity log, so "since you left" is everything after it. A handover note
is generated from the log and the current state, edited by the person handing over, and then kept as
posted: like an assessment, it's never edited afterwards.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ostraka.db import Base
from ostraka.models.people import User, utcnow


class IncidentView(Base):
    """The last activity-log position a person had seen on an incident, and when."""

    __tablename__ = "incident_views"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    last_position: Mapped[int] = mapped_column(Integer, default=0)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HandoverNote(Base):
    """A shift handover as posted. `generated` is what Ostraka drafted, `body` what was posted, so
    anyone can see what the person handing over changed."""

    __tablename__ = "handover_notes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # HND-3
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # The stretch of the activity log it covers: after `from_position`, up to and including `through`
    from_position: Mapped[int] = mapped_column(Integer)
    through_position: Mapped[int] = mapped_column(Integer)
    generated: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    author: Mapped[User | None] = relationship(lazy="joined")
