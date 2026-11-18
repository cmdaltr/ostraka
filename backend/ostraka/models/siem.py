"""SIEM queries (`ostraka/siem.py`): the connections an admin adds, and every query run through them.

Only the query is kept, never what it returned: its text, where and when it ran, for which record, by
whom, and how many rows came back. The rows go to the person who ran it and nowhere else.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ostraka.db import Base
from ostraka.models.people import _enum, utcnow


class SiemKind(StrEnum):
    SPLUNK = "splunk"  # SPL
    ELASTIC = "elastic"  # ES|QL
    DEFENDER = "defender"  # KQL, Advanced Hunting
    SECOPS = "secops"  # UDM search


class QueryOutcome(StrEnum):
    OK = "ok"
    FAILED = "failed"


class SiemConnection(Base):
    """One SIEM an admin has connected. `settings` holds what isn't secret (its URL, tenant, project);
    `secret` the credentials, encrypted, never shown again."""

    __tablename__ = "siem_connections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[SiemKind] = mapped_column(_enum(SiemKind, "siem_kind"))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    secret: Mapped[str] = mapped_column(Text, default="")  # encrypted JSON
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_ok: Mapped[bool | None] = mapped_column(Boolean)
    check_message: Mapped[str] = mapped_column(String(300), default="")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)


class SiemQuery(Base):
    """One query as it was run: never its results."""

    __tablename__ = "siem_queries"
    __table_args__ = (
        # found by any part of its text (pg_trgm, migration 0024)
        Index(
            "ix_siem_queries_text_trgm",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # QRY-12
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[SiemKind] = mapped_column(_enum(SiemKind, "siem_kind"))
    # the connection's name as it was, so the history still says where after it's removed
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("siem_connections.id", ondelete="SET NULL")
    )
    connection_name: Mapped[str] = mapped_column(String(80))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[QueryOutcome] = mapped_column(_enum(QueryOutcome, "query_outcome"))
    row_count: Mapped[int | None] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    run_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    @property
    def ref(self) -> str:
        return f"QRY-{self.seq}"
