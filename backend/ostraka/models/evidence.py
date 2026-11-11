"""Evidence items (exhibits) and their chain of custody.

An exhibit's hashes are fixed when it's recorded: a wrong hash means a new exhibit, never an edit.
Custody is a list of entries that is only ever added to. Each entry's hash covers the one before it,
and the first covers the exhibit's identity and hashes, so a changed hash or a rewritten handover
shows when the chain is verified. Every entry is also an event on the incident's activity chain,
which carries the entry's hash, and the database refuses to update or delete an entry directly.

Handlers are named in the entry as well as by id: the record has to read the same after an account
is renamed or removed, so the ids are plain columns rather than foreign keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ostraka.db import Base
from ostraka.models.people import _enum, utcnow


class CustodyKind(StrEnum):
    RECEIVED = "received"  # the first custodian took it: acquisition or receipt into the incident
    OFFERED = "offered"  # the holder offered it to someone; nothing moves until they accept
    ACCEPTED = "accepted"  # the recipient took it, with their acknowledgement
    DECLINED = "declined"  # the recipient refused it
    WITHDRAWN = "withdrawn"  # the offer was taken back before it was answered


class HashCheck(StrEnum):
    """What the receiver found when they re-hashed the item on receipt."""

    MATCH = "match"
    MISMATCH = "mismatch"


class EvidenceItem(Base):
    """An exhibit: something held as evidence, identified by its hashes, with where it is kept."""

    __tablename__ = "evidence_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # EXH-4
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64))
    sha1: Mapped[str] = mapped_column(String(40), default="")
    md5: Mapped[str] = mapped_column(String(32), default="")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    # Where it's held when recorded; each accepted handover may say where it went next
    location: Mapped[str] = mapped_column(String(500))
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    entries: Mapped[list[CustodyEntry]] = relationship(
        lazy="selectin", order_by="CustodyEntry.position", passive_deletes=True
    )

    @property
    def ref(self) -> str:
        return f"EXH-{self.seq}"


class CustodyEntry(Base):
    """One step in an exhibit's custody. Never changed once written."""

    __tablename__ = "custody_entries"
    __table_args__ = (UniqueConstraint("evidence_id", "position", name="uq_custody_entries_position"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)  # 1, 2, 3… within the exhibit
    kind: Mapped[CustodyKind] = mapped_column(_enum(CustodyKind, "custody_kind"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # when it happened, as stated
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_name: Mapped[str] = mapped_column(String(120))
    from_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    from_name: Mapped[str] = mapped_column(String(120), default="")
    to_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    to_name: Mapped[str] = mapped_column(String(120), default="")
    purpose: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(500), default="")
    # The receiver's own words on taking it: the seal, the state it was in, what they checked
    acknowledgement: Mapped[str] = mapped_column(Text, default="")
    hash_check: Mapped[HashCheck | None] = mapped_column(_enum(HashCheck, "hash_check"))
    offer_position: Mapped[int | None] = mapped_column(Integer)  # the offer an answer refers to
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class FindingEvidence(Base):
    """A finding citing an exhibit."""

    __tablename__ = "finding_evidence"

    finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    cited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
