"""An incident read once for the intelligence exports, as plain values the formats can be tested on."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import branding, licence, notify
from ostraka.incidents.masking import mask_text
from ostraka.models import (
    Finding,
    FindingKind,
    FindingStatus,
    Incident,
    IncidentObservable,
    RecordKind,
    Severity,
    Tlp,
    Verdict,
)
from ostraka.observables.detect import ObservableType

CONFIRMED = "confirmed"
LEAD = "lead"


@dataclass(frozen=True)
class IntelSighting:
    id: uuid.UUID
    seen_at: datetime
    host: str = ""
    user_account: str = ""
    source: str = ""


@dataclass(frozen=True)
class IntelFinding:
    id: uuid.UUID
    ref: str  # FND-12
    kind: FindingKind
    title: str  # masked
    statement: str  # masked
    status: FindingStatus  # lead or confirmed; refuted findings never get this far
    techniques: tuple[str, ...]
    observable_ids: tuple[uuid.UUID, ...]  # incident observable ids
    created_at: datetime
    decided_at: datetime | None = None
    url: str = ""

    @property
    def standing(self) -> str:
        return CONFIRMED if self.status is FindingStatus.CONFIRMED else LEAD


@dataclass(frozen=True)
class IntelObservable:
    id: uuid.UUID  # the incident observable's id
    ref: str  # OBS-311
    kind: ObservableType
    value: str  # as recorded: indicators go out raw, in their typed place
    verdict: Verdict
    added_at: datetime
    note: str = ""  # masked
    tags: tuple[str, ...] = ()
    sightings: tuple[IntelSighting, ...] = ()
    cited_by: tuple[str, ...] = ()  # refs of the findings that cite it
    confirmed: bool = False

    @property
    def standing(self) -> str:
        return CONFIRMED if self.confirmed else LEAD

    @property
    def first_seen(self) -> datetime:
        return min((s.seen_at for s in self.sightings), default=self.added_at)

    @property
    def last_seen(self) -> datetime:
        return max((s.seen_at for s in self.sightings), default=self.added_at)


@dataclass(frozen=True)
class IntelRecord:
    id: uuid.UUID
    ref: str
    kind: RecordKind
    title: str  # masked
    summary: str  # masked
    severity: Severity
    tlp: Tlp
    declared_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    url: str
    producer: str  # who the bundle is from: the licensee, or the product's name
    findings: tuple[IntelFinding, ...] = ()
    observables: tuple[IntelObservable, ...] = ()
    producer_key: str = field(default="")  # what the producer's identity id is derived from

    @property
    def restricted(self) -> bool:
        return self.tlp in (Tlp.RED, Tlp.AMBER_STRICT)

    def techniques(self) -> dict[str, list[IntelFinding]]:
        """{ATT&CK ID: the findings naming it}, confirmed findings first, in ID order."""
        out: dict[str, list[IntelFinding]] = {}
        for finding in self.findings:
            for tid in finding.techniques:
                out.setdefault(tid, []).append(finding)
        for tid in out:
            out[tid].sort(key=lambda f: (f.standing != CONFIRMED, f.ref))
        return dict(sorted(out.items()))

    def technique_standing(self, tid: str) -> str:
        return CONFIRMED if any(f.standing == CONFIRMED for f in self.techniques().get(tid, [])) else LEAD


def is_confirmed_indicator(verdict: Verdict, citing: list[FindingStatus]) -> bool:
    """An indicator is confirmed only when it's judged malicious and a confirmed finding cites it.
    A malicious verdict alone is an analyst's view of the value, not a verified finding."""
    return verdict is Verdict.MALICIOUS and FindingStatus.CONFIRMED in citing


async def gather(db: AsyncSession, incident: Incident) -> IntelRecord:
    """Read what the exports carry: findings that aren't refuted, observables that aren't benign."""
    findings = (
        await db.scalars(
            select(Finding)
            .where(Finding.incident_id == incident.id, Finding.status != FindingStatus.REFUTED)
            .order_by(Finding.seq)
        )
    ).all()
    links = (
        await db.scalars(
            select(IncidentObservable)
            .where(
                IncidentObservable.incident_id == incident.id, IncidentObservable.verdict != Verdict.BENIGN
            )
            .order_by(IncidentObservable.added_at)
        )
    ).all()
    citing: dict[uuid.UUID, list[Finding]] = {}
    for f in findings:
        for link in f.observable_links:
            citing.setdefault(link.incident_observable_id, []).append(f)

    standing = await licence.current(db)
    producer = standing.organisation or branding.NAME
    return IntelRecord(
        id=incident.id,
        ref=incident.ref,
        kind=incident.kind,
        title=mask_text(incident.title),
        summary=mask_text(incident.summary or ""),
        severity=incident.severity,
        tlp=incident.tlp,
        declared_at=incident.declared_at,
        updated_at=incident.updated_at,
        closed_at=incident.closed_at,
        url=notify.link_url(f"/incidents/{incident.ref}"),
        producer=producer,
        producer_key=f"{notify.link_url('/')}|{producer}",
        findings=tuple(
            IntelFinding(
                id=f.id,
                ref=f"FND-{f.seq}",
                kind=f.kind,
                title=mask_text(f.title),
                statement=mask_text(f.statement or ""),
                status=f.status,
                techniques=tuple(f.techniques or ()),
                observable_ids=tuple(link.incident_observable_id for link in f.observable_links),
                created_at=f.created_at,
                decided_at=f.decided_at,
                url=notify.link_url(notify.incident_link(incident, "findings", f"FND-{f.seq}")),
            )
            for f in findings
        ),
        observables=tuple(
            IntelObservable(
                id=item.id,
                ref=f"OBS-{item.observable.seq}",
                kind=item.observable.kind,
                value=item.observable.value,
                verdict=item.verdict,
                added_at=item.added_at,
                note=mask_text(item.note or ""),
                tags=tuple(item.tags or ()),
                sightings=tuple(
                    IntelSighting(
                        id=s.id,
                        seen_at=s.seen_at or s.recorded_at,
                        host=mask_text(s.host or ""),
                        user_account=mask_text(s.user_account or ""),
                        source=mask_text(s.source or ""),
                    )
                    for s in item.sightings
                ),
                cited_by=tuple(f"FND-{f.seq}" for f in citing.get(item.id, [])),
                confirmed=is_confirmed_indicator(item.verdict, [f.status for f in citing.get(item.id, [])]),
            )
            for item in links
        ),
    )
