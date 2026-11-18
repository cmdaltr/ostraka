"""The attack flow's decisions: what an analyst has said about a stage of an incident's attack flow.

The flow itself is read from the record each time (`ostraka/flow.py`); only what a person decides is
stored: a stage they expect that Ostraka didn't, one they've ruled out as not applicable, or one they've
checked and found nothing in, each with their reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ostraka.db import Base
from ostraka.models.people import _enum, utcnow


class StageCall(StrEnum):
    EXPECTED = "expected"  # an analyst expects this stage, whatever ATT&CK suggests
    DISMISSED = "dismissed"  # not applicable here, with the reason
    CLEARED = "cleared"  # checked and nothing found, naming where


class FlowStage(Base):
    """One analyst decision on one stage of one incident's flow."""

    __tablename__ = "flow_stages"
    __table_args__ = (UniqueConstraint("incident_id", "stage", name="uq_flow_stages_incident_stage"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(32))  # a key of ostraka.flow.STAGES
    call: Mapped[StageCall] = mapped_column(_enum(StageCall, "stage_call"))
    note: Mapped[str] = mapped_column(Text, default="")
    by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
