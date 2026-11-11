"""Incidents and everything recorded in one: teams and roles, observables and sightings, assets,
findings and their verifications, comments, and the hash-chained activity log."""

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
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ostraka.db import Base
from ostraka.models.people import Team, User, _enum, utcnow
from ostraka.observables.detect import ObservableType


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Tlp(StrEnum):
    RED = "red"
    AMBER_STRICT = "amber+strict"
    AMBER = "amber"
    GREEN = "green"
    CLEAR = "clear"


class RecordKind(StrEnum):
    """What a record is: an incident being responded to, or a hunt looking for one."""

    INCIDENT = "incident"
    HUNT = "hunt"


class Phase(StrEnum):
    DETECT = "detect"
    SCOPE = "scope"
    CONTAIN = "contain"
    ERADICATE = "eradicate"
    RECOVER = "recover"
    LEARN = "learn"
    # A hunt's phases
    HYPOTHESIS = "hypothesis"
    COLLECT = "collect"
    ANALYSE = "analyse"
    CONCLUDE = "conclude"


PHASES_OF = {
    RecordKind.INCIDENT: (
        Phase.DETECT,
        Phase.SCOPE,
        Phase.CONTAIN,
        Phase.ERADICATE,
        Phase.RECOVER,
        Phase.LEARN,
    ),
    RecordKind.HUNT: (Phase.HYPOTHESIS, Phase.COLLECT, Phase.ANALYSE, Phase.CONCLUDE),
}


class LinkType(StrEnum):
    """How two records relate, read from source to target."""

    PARENT = "parent"  # the source is the target's parent
    RELATED = "related"
    DUPLICATE = "duplicate"  # the source duplicates the target, which is the one worked
    PROMOTED = "promoted"  # the source hunt was promoted to the target incident


class IncidentStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class IncidentRole(StrEnum):
    COMMANDER = "commander"
    COMMS = "comms"
    SCRIBE = "scribe"
    RESPONDER = "responder"


class Verdict(StrEnum):
    UNKNOWN = "unknown"
    SUSPICIOUS = "suspicious"
    MALICIOUS = "malicious"
    BENIGN = "benign"


class AssetStatus(StrEnum):
    IDENTIFIED = "identified"
    INVESTIGATING = "investigating"
    CONTAINED = "contained"
    ERADICATED = "eradicated"
    RECOVERED = "recovered"
    CLEAN = "clean"


class LinkKind(StrEnum):
    LATERAL_MOVEMENT = "lateral_movement"
    C2 = "c2"
    EXFILTRATION = "exfiltration"
    OTHER = "other"


class FindingKind(StrEnum):
    """From the original brief: evidence seen in this incident, intelligence from outside it, and
    potentials still to be proven."""

    EVIDENCE = "evidence"
    INTELLIGENCE = "intelligence"
    POTENTIAL = "potential"


class FindingStatus(StrEnum):
    LEAD = "lead"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"


class VerificationMethod(StrEnum):
    SEARCH = "search"  # a query run on a named platform over a stated scope
    ROWANHOLT = "rowanholt"  # a cited Rowanholt row
    OTHER = "other"


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (UniqueConstraint("kind", "year", "number", name="uq_incidents_kind_year_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[RecordKind] = mapped_column(_enum(RecordKind, "record_kind"), default=RecordKind.INCIDENT)
    year: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)  # within the kind and year: OST-2026-014, HNT-2026-003
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[Severity] = mapped_column(_enum(Severity, "severity"))
    tlp: Mapped[Tlp] = mapped_column(_enum(Tlp, "tlp"))
    phase: Mapped[Phase] = mapped_column(_enum(Phase, "phase"), default=Phase.DETECT)
    status: Mapped[IncidentStatus] = mapped_column(
        _enum(IncidentStatus, "incident_status"), default=IncidentStatus.OPEN
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    # Only the commander may export a sensitive incident's reports
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    # The report brand its exports use (MSSP); none means the installation's default
    brand_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("report_brands.id", ondelete="SET NULL"))

    declared_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    declared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # When the organisation became aware: regulator clocks (phase 4) run from here
    aware_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    teams: Mapped[list[IncidentTeam]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    members: Mapped[list[IncidentMember]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    @property
    def ref(self) -> str:
        from ostraka import branding

        prefix = branding.HUNT_PREFIX if self.kind is RecordKind.HUNT else branding.INCIDENT_PREFIX
        return f"{prefix}-{self.year}-{self.number:03d}"


class IncidentCounter(Base):
    """The last number used for a kind of record in a year; locked while a number is taken."""

    __tablename__ = "incident_counters"

    kind: Mapped[RecordKind] = mapped_column(_enum(RecordKind, "record_kind"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last: Mapped[int] = mapped_column(Integer, default=0)


class IncidentTeam(Base):
    __tablename__ = "incident_teams"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    team: Mapped[Team] = relationship(lazy="joined")


class IncidentMember(Base):
    """Someone added to an incident by name, or holding one of its roles."""

    __tablename__ = "incident_members"
    __table_args__ = (
        Index(
            "uq_incident_members_one_commander",
            "incident_id",
            unique=True,
            postgresql_where=text("role = 'commander'"),
        ),
    )

    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[IncidentRole] = mapped_column(
        _enum(IncidentRole, "incident_role"), default=IncidentRole.RESPONDER
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(lazy="joined")


class Observable(Base):
    """One indicator, held once across every incident, so overlaps between incidents show."""

    __tablename__ = "observables"
    __table_args__ = (UniqueConstraint("kind", "value_key", name="uq_observables_kind_value"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # OBS-311
    kind: Mapped[ObservableType] = mapped_column(_enum(ObservableType, "observable_kind"))
    value: Mapped[str] = mapped_column(Text)
    value_key: Mapped[str] = mapped_column(
        Text
    )  # how equality is judged: lower-cased where case doesn't matter
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IncidentObservable(Base):
    """An observable as it appears in one incident: the verdict, notes and tags there."""

    __tablename__ = "incident_observables"
    __table_args__ = (UniqueConstraint("incident_id", "observable_id", name="uq_incident_observables_pair"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    observable_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("observables.id", ondelete="RESTRICT"), index=True
    )
    verdict: Mapped[Verdict] = mapped_column(_enum(Verdict, "verdict"), default=Verdict.UNKNOWN)
    note: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    observable: Mapped[Observable] = relationship(lazy="joined")
    sightings: Mapped[list[Sighting]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="Sighting.seen_at"
    )


class Sighting(Base):
    """Where and when an observable was seen: on which host, by which account, from which source."""

    __tablename__ = "sightings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_observable_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incident_observables.id", ondelete="CASCADE"), index=True
    )
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when it happened
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    host: Mapped[str] = mapped_column(String(255), default="")  # when no asset record exists yet
    user_account: Mapped[str] = mapped_column(String(255), default="")
    source: Mapped[str] = mapped_column(String(255), default="")  # e.g. "Defender XDR", "Rowanholt job 41"
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Asset(Base):
    """A system of interest in an incident. At most one per incident is patient zero."""

    __tablename__ = "assets"
    __table_args__ = (
        Index(
            "uq_assets_one_patient_zero",
            "incident_id",
            unique=True,
            postgresql_where=text("is_patient_zero"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # AST-27
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    hostname: Mapped[str] = mapped_column(String(255))
    ip: Mapped[str] = mapped_column(String(64), default="")
    public_ip: Mapped[str] = mapped_column(String(64), default="")
    os: Mapped[str] = mapped_column(String(120), default="")
    primary_user: Mapped[str] = mapped_column(String(255), default="")
    purpose: Mapped[str] = mapped_column(Text, default="")
    data_types: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)  # e.g. PII, payroll
    criticality: Mapped[Severity] = mapped_column(
        _enum(Severity, "asset_criticality"), default=Severity.MEDIUM
    )
    status: Mapped[AssetStatus] = mapped_column(
        _enum(AssetStatus, "asset_status"), default=AssetStatus.IDENTIFIED
    )
    is_patient_zero: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)


class AssetLink(Base):
    """An asset connection: lateral movement, command and control or exfiltration between two assets,
    with when it was seen and where."""

    __tablename__ = "asset_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    kind: Mapped[LinkKind] = mapped_column(_enum(LinkKind, "link_kind"))
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    technique: Mapped[str] = mapped_column(String(16), default="")
    # Where the connection was seen: "Defender XDR", "FW01 logs", "Rowanholt job 41"
    source: Mapped[str] = mapped_column(String(255), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Finding(Base):
    """A claim about the incident. It starts as a lead; confirming it needs a verification."""

    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # FND-12
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[FindingKind] = mapped_column(_enum(FindingKind, "finding_kind"))
    title: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[FindingStatus] = mapped_column(
        _enum(FindingStatus, "finding_status"), default=FindingStatus.LEAD
    )
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    techniques: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)

    # Admiralty grading and UK policing handling code, for intelligence
    source_reliability: Mapped[str | None] = mapped_column(String(1))  # A–F
    information_credibility: Mapped[str | None] = mapped_column(String(1))  # 1–6
    handling_code: Mapped[str | None] = mapped_column(String(1))  # P or C
    handling_conditions: Mapped[str] = mapped_column(Text, default="")

    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)

    verifications: Mapped[list[Verification]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="Verification.ran_at"
    )
    observable_links: Mapped[list[FindingObservable]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class FindingObservable(Base):
    __tablename__ = "finding_observables"

    finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), primary_key=True
    )
    incident_observable_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incident_observables.id", ondelete="CASCADE"), primary_key=True
    )


class Verification(Base):
    """The check behind a finding: a search over a stated scope, or a cited Rowanholt row.
    Recorded separately from the finding's author: evidence must be verified, not just provided."""

    __tablename__ = "verifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    method: Mapped[VerificationMethod] = mapped_column(_enum(VerificationMethod, "verification_method"))
    platform: Mapped[str] = mapped_column(String(120), default="")  # e.g. Defender XDR, Splunk
    query: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(500))  # the host or source searched, and nothing wider
    time_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_count: Mapped[int | None] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(500), default="")  # Rowanholt job/host/artefact/row
    note: Mapped[str] = mapped_column(Text, default="")
    ran_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Comment(Base):
    """A comment on the incident or on something in it. Replies hang off a top-level comment."""

    __tablename__ = "comments"
    __table_args__ = (Index("ix_comments_target", "incident_id", "target_type", "target_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    target_type: Mapped[str] = mapped_column(String(24))  # incident | finding | asset | observable | task
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    author: Mapped[User | None] = relationship(lazy="joined", foreign_keys=[author_id])


class Mention(Base):
    """A person or team named in a comment. Phase 3's notifications are sent from these."""

    __tablename__ = "mentions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    comment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)


class TagDefinition(Base):
    """The controlled vocabulary. Free tags are allowed too; these are the ones with agreed meaning."""

    __tablename__ = "tag_definitions"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="")


class ActivityEvent(Base):
    """Append-only record of everything done in an incident. Each event's hash covers the previous
    one, so an edit or deletion anywhere in the chain shows when the chain is verified."""

    __tablename__ = "activity_events"
    __table_args__ = (UniqueConstraint("incident_id", "position", name="uq_activity_incident_position"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # 1, 2, 3… within the incident
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(48))
    entity_type: Mapped[str] = mapped_column(String(24))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    summary: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))

    actor: Mapped[User | None] = relationship(lazy="joined")


class IncidentLink(Base):
    """Two records joined: parent and child, related, duplicate, or a hunt promoted to an incident."""

    __tablename__ = "incident_links"
    __table_args__ = (
        UniqueConstraint("source_id", "target_id", name="uq_incident_links_pair"),
        # A record has at most one parent
        Index(
            "uq_incident_links_one_parent", "target_id", unique=True, postgresql_where=text("kind = 'parent'")
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[LinkType] = mapped_column(_enum(LinkType, "link_type"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
