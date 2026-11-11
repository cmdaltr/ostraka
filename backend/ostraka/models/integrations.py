"""The suite links (`ostraka.integrations`): the Rowanholt and Shikari servers an admin connects,
incidents linked to Rowanholt jobs and cases, findings citing Rowanholt rows, and the detection gaps
a confirmed technique opens until Shikari has a detection for it."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from ostraka.db import Base
from ostraka.models.people import _enum, utcnow


class Product(StrEnum):
    ROWANHOLT = "rowanholt"
    SHIKARI = "shikari"


class GapStatus(StrEnum):
    OPEN = "open"  # no detection in Shikari for the technique, as far as Ostraka knows
    CLOSED = "closed"  # Shikari answered that it has one


class Integration(Base):
    """One sibling server for the installation. Its token and webhook secret are stored encrypted."""

    __tablename__ = "integrations"

    product: Mapped[Product] = mapped_column(_enum(Product, "integration_product"), primary_key=True)
    url: Mapped[str] = mapped_column(String(2000))
    token: Mapped[str] = mapped_column(Text, default="")  # encrypted
    webhook_secret: Mapped[str] = mapped_column(Text, default="")  # encrypted; Shikari's webhooks to us
    # Shikari: whose queue a detection gap's task goes to
    detection_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_ok: Mapped[bool | None] = mapped_column(Boolean)
    check_message: Mapped[str] = mapped_column(String(300), default="")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)


class RowanholtLink(Base):
    """An incident linked to a Rowanholt job, or to a case number (all its jobs). The status is the
    last one Rowanholt gave, kept so the incident still shows something when it can't be reached."""

    __tablename__ = "rowanholt_links"
    __table_args__ = (
        UniqueConstraint("incident_id", "job_id", "case_number", name="uq_rowanholt_links_target"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(String(64), default="")  # empty: the link is to a case number
    case_number: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(32), default="")
    progress: Mapped[int | None] = mapped_column(Integer)
    # A case's jobs as last seen: [{"id", "status", "created_at"}]
    jobs: Mapped[list] = mapped_column(JSON, default=list)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    linked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RowanholtCitation(Base):
    """A Rowanholt row a finding cites: job, host, artefact, row reference and the row's hash, with
    a copy of the row as it read then. An imported Rowanholt finding arrives as a lead with its
    citation, `remote_finding_id` set so it isn't imported twice."""

    __tablename__ = "rowanholt_citations"
    __table_args__ = (
        Index(
            "uq_rowanholt_citations_remote",
            "incident_id",
            "remote_finding_id",
            unique=True,
            postgresql_where=sql_text("remote_finding_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    verification_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("verifications.id", ondelete="SET NULL")
    )
    job_id: Mapped[str] = mapped_column(String(64))
    case_number: Mapped[str] = mapped_column(String(200), default="")
    host: Mapped[str] = mapped_column(String(255))
    artefact: Mapped[str] = mapped_column(String(1000))
    row_ref: Mapped[str] = mapped_column(String(32))
    row_hash: Mapped[str] = mapped_column(String(64), default="")
    excerpt: Mapped[dict] = mapped_column(JSON, default=dict)  # the row's cells when it was cited
    remote_finding_id: Mapped[str | None] = mapped_column(String(64))
    remote_status: Mapped[str] = mapped_column(String(16), default="")  # what Rowanholt called it
    cited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DetectionGap(Base):
    """An ATT&CK technique a confirmed finding showed, tracked until Shikari has a detection for it.
    Only Shikari's answer closes one: nobody can close it by hand."""

    __tablename__ = "detection_gaps"
    __table_args__ = (UniqueConstraint("incident_id", "technique", name="uq_detection_gaps_technique"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    technique: Mapped[str] = mapped_column(String(16))
    technique_name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[GapStatus] = mapped_column(_enum(GapStatus, "gap_status"), default=GapStatus.OPEN)
    finding_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("findings.id", ondelete="SET NULL"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    opened_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_error: Mapped[str] = mapped_column(String(300), default="")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str] = mapped_column(String(200), default="")  # how Shikari said so
