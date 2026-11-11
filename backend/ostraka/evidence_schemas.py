"""Request and response bodies for evidence items, their chain of custody, and the incident graph."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ostraka import custody
from ostraka.incident_schemas import Person, Versioned
from ostraka.models import CustodyKind, HashCheck, Tlp

# ---- evidence items -------------------------------------------------------------------------------


class EvidenceIn(BaseModel):
    label: str = Field(min_length=3, max_length=200, description="What it is: 'FIN-WS-0142 disk image'")
    description: str = Field(default="", max_length=20_000)
    sha256: str = Field(description="Required: the SHA-256 taken at acquisition")
    sha1: str = ""
    md5: str = ""
    size_bytes: int | None = Field(default=None, ge=0)
    location: str = Field(min_length=1, max_length=500, description="Where it's held: safe, bag, share")
    acquired_at: datetime | None = None
    holder_id: uuid.UUID | None = Field(
        default=None, description="Who holds it first; you, when left out. They needn't accept."
    )

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, v: str) -> str:
        return custody.clean_hash(v, custody.SHA256, "SHA-256", required=True)

    @field_validator("sha1")
    @classmethod
    def _sha1(cls, v: str) -> str:
        return custody.clean_hash(v, custody.SHA1, "SHA-1")

    @field_validator("md5")
    @classmethod
    def _md5(cls, v: str) -> str:
        return custody.clean_hash(v, custody.MD5, "MD5")


class EvidencePatch(Versioned):
    """Only the words can change. Hashes are fixed; where it's held moves with each handover."""

    label: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)


class OfferIn(BaseModel):
    to_id: uuid.UUID
    purpose: str = Field(min_length=3, max_length=2000, description="Why it's moving: analysis, legal hold")
    location: str = Field(default="", max_length=500, description="Where it'll be held; unchanged if empty")
    at: datetime | None = Field(default=None, description="When it's handed over; now if left out")


class AcceptIn(BaseModel):
    acknowledgement: str = Field(
        min_length=3,
        max_length=5000,
        description="The receiver's own words: the seal, the state it arrived in, what they checked",
    )
    sha256: str = Field(default="", description="The SHA-256 the receiver computed, if they re-hashed it")
    at: datetime | None = None

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, v: str) -> str:
        return custody.clean_hash(v, custody.SHA256, "SHA-256")


class AnswerIn(BaseModel):
    note: str = Field(default="", max_length=5000)


class CustodyEntryOut(BaseModel):
    position: int
    kind: CustodyKind
    at: datetime
    recorded_at: datetime
    actor: Person | None
    from_person: Person | None
    to_person: Person | None
    purpose: str
    location: str
    acknowledgement: str
    hash_check: HashCheck | None
    offer_position: int | None
    hash: str


class ChainOut(BaseModel):
    ok: bool
    entries: int
    broken_at: int | None
    head: str


class EvidenceOut(BaseModel):
    id: uuid.UUID
    ref: str
    label: str
    description: str
    # Withheld from observers who haven't asked to see indicators: a sample's hash is one
    sha256: str | None
    sha1: str | None
    md5: str | None
    masked: bool
    size_bytes: int | None
    location: str
    acquired_at: datetime | None
    created_by: Person | None
    created_at: datetime
    holder: Person | None
    pending: CustodyEntryOut | None
    hash_mismatch: bool
    cited_by: list[dict[str, str]]  # [{"id", "ref"}] of findings
    entries: list[CustodyEntryOut]
    chain: ChainOut
    can_offer: bool
    can_answer: bool
    can_withdraw: bool
    version: int


class CiteIn(BaseModel):
    evidence_id: uuid.UUID


# ---- the graph ------------------------------------------------------------------------------------

NodeType = Literal["observable", "asset", "technique", "finding", "evidence"]
EdgeKind = Literal[
    "sighted_on",  # observable → asset
    "cites_observable",  # finding → observable
    "about_asset",  # finding → asset
    "uses_technique",  # finding → technique
    "cites_evidence",  # finding → evidence
    "connection",  # asset → asset: lateral movement, C2, exfiltration
    "connection_technique",  # asset → technique, from a connection's technique
]


class GraphNode(BaseModel):
    id: str  # "<type>:<uuid>", or "technique:T1021.002"
    type: NodeType
    ref: str  # OBS-3, AST-2, FND-7, EXH-1, or the technique ID
    label: str
    entity_id: str | None  # the record's id; None for a technique
    attrs: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    kind: EdgeKind
    attrs: dict[str, Any] = Field(default_factory=dict)


class TechniqueUse(BaseModel):
    """One technique as the incident shows it, for an ATT&CK Navigator layer."""

    technique_id: str
    name: str
    tactics: list[str]  # ATT&CK shortnames, e.g. "lateral-movement"
    confirmed: int  # confirmed findings using it
    leads: int  # findings still leads
    refuted: int
    connections: int  # asset connections recorded with it
    findings: list[str]  # finding refs, refuted ones included
    first_seen: datetime | None


class GraphOut(BaseModel):
    incident_id: uuid.UUID
    ref: str
    tlp: Tlp
    restricted: bool  # TLP:RED or TLP:AMBER+STRICT: keep it out of anything that leaves Ostraka
    masked: bool  # indicator values withheld for this viewer
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    techniques: list[TechniqueUse]
    attack_version: str | None
