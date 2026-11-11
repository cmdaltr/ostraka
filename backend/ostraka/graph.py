"""The incident graph: observables, assets, ATT&CK techniques, findings and exhibits as nodes, joined
by what's already recorded. Nothing here is stored; the graph is read from the record each time.

Edges, read from source to target:
    sighted_on            observable → asset      its sightings on that asset (by asset, or by hostname)
    cites_observable      finding → observable
    about_asset           finding → asset
    uses_technique        finding → technique
    cites_evidence        finding → exhibit
    connection            asset → asset           lateral movement, C2 or exfiltration
    connection_technique  asset → technique       the technique a connection from that asset used

`techniques` sums each technique up for an ATT&CK Navigator layer: how many confirmed findings, leads
and refuted findings use it, and how many connections. `build` is pure, so it's unit-tested without
a database; `incident_graph` reads the rows.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import custody, notify
from ostraka.evidence_schemas import GraphEdge, GraphNode, GraphOut, TechniqueUse
from ostraka.incidents.masking import for_viewer, hides_iocs
from ostraka.models import (
    Asset,
    AssetLink,
    EvidenceItem,
    Finding,
    FindingEvidence,
    Incident,
    IncidentObservable,
    User,
)
from ostraka.reports import attack


def _id(kind: str, value: Any) -> str:
    return f"{kind}:{value}"


def _earliest(*moments: datetime | None) -> datetime | None:
    present = [m for m in moments if m is not None]
    return min(present) if present else None


def build(
    incident: Incident,
    *,
    masked: bool,
    mask: Callable[[str], str],
    observables: Iterable[IncidentObservable],
    assets: Iterable[Asset],
    connections: Iterable[AssetLink],
    findings: Iterable[Finding],
    citations: Iterable[tuple[uuid.UUID, uuid.UUID]],
    exhibits: Iterable[EvidenceItem],
) -> GraphOut:
    """The graph as one viewer may see it. `masked` withholds indicator values, and `mask` is applied
    to every piece of free text, as the rest of the API does for that viewer."""
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}

    def edge(kind: str, start: str, end: str, key: str = "", /, **attrs: Any) -> None:
        edge_id = f"{kind}:{start}>{end}" + (f":{key}" if key else "")
        edges[edge_id] = GraphEdge(id=edge_id, source=start, target=end, kind=kind, attrs=attrs)

    def technique(tid: str) -> str:
        node_id = _id("technique", tid)
        if node_id not in nodes:
            name, tactics = attack.technique(tid)
            nodes[node_id] = GraphNode(
                id=node_id,
                type="technique",
                ref=tid,
                label=f"{tid} {name}".strip(),
                entity_id=None,
                attrs={"name": name, "tactics": tactics},
            )
        return node_id

    assets = list(assets)
    by_host = {a.hostname.strip().lower(): a for a in assets if a.hostname.strip()}
    for a in assets:
        nodes[_id("asset", a.id)] = GraphNode(
            id=_id("asset", a.id),
            type="asset",
            ref=f"AST-{a.seq}",
            label=a.hostname,
            entity_id=str(a.id),
            attrs={
                "patient_zero": a.is_patient_zero,
                "status": a.status.value,
                "criticality": a.criticality.value,
            },
        )

    for item in observables:
        kind = item.observable.kind.value
        node_id = _id("observable", item.id)
        nodes[node_id] = GraphNode(
            id=node_id,
            type="observable",
            ref=f"OBS-{item.observable.seq}",
            label=f"[{kind.replace('_', ' ')}]" if masked else item.observable.value,
            entity_id=str(item.id),
            attrs={"kind": kind, "verdict": item.verdict.value, "masked": masked},
        )
        on: dict[uuid.UUID, list] = defaultdict(list)
        for s in item.sightings:
            asset = None
            if s.asset_id is not None:
                asset = next((a for a in assets if a.id == s.asset_id), None)
            elif s.host:
                asset = by_host.get(s.host.strip().lower())
            if asset is not None:
                on[asset.id].append(s)
        for asset_id, seen in on.items():
            times = [s.seen_at for s in seen if s.seen_at]
            edge(
                "sighted_on",
                node_id,
                _id("asset", asset_id),
                sightings=len(seen),
                first_seen=min(times) if times else None,
                last_seen=max(times) if times else None,
                sources=sorted({s.source for s in seen if s.source}),
            )

    exhibits = list(exhibits)
    for x in exhibits:
        now = custody.state(x, list(x.entries))
        nodes[_id("evidence", x.id)] = GraphNode(
            id=_id("evidence", x.id),
            type="evidence",
            ref=x.ref,
            label=mask(x.label),
            entity_id=str(x.id),
            attrs={
                "holder": now.holder_name,
                "handover_pending": now.pending is not None,
                "hash_mismatch": now.mismatch,
            },
        )

    uses: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"confirmed": 0, "lead": 0, "refuted": 0, "connections": 0, "findings": [], "first": []}
    )
    cited = defaultdict(list)
    for finding_id, evidence_id in citations:
        cited[finding_id].append(evidence_id)
    for f in findings:
        node_id = _id("finding", f.id)
        state = f.status.value
        nodes[node_id] = GraphNode(
            id=node_id,
            type="finding",
            ref=f"FND-{f.seq}",
            label=mask(f.title),
            entity_id=str(f.id),
            attrs={"status": state, "kind": f.kind.value, "occurred_at": f.occurred_at},
        )
        for link in f.observable_links:
            target = _id("observable", link.incident_observable_id)
            if target in nodes:
                edge("cites_observable", node_id, target, status=state)
        if f.asset_id is not None and _id("asset", f.asset_id) in nodes:
            edge("about_asset", node_id, _id("asset", f.asset_id), status=state)
        for tid in f.techniques or []:
            edge("uses_technique", node_id, technique(tid), status=state)
            use = uses[tid]
            use[state] += 1
            use["findings"].append(f"FND-{f.seq}")
            use["first"].append(f.occurred_at)
        for evidence_id in cited.get(f.id, []):
            if _id("evidence", evidence_id) in nodes:
                edge("cites_evidence", node_id, _id("evidence", evidence_id), status=state)

    for c in connections:
        source, target = _id("asset", c.source_id), _id("asset", c.target_id)
        if source not in nodes or target not in nodes:
            continue
        edge(
            "connection",
            source,
            target,
            str(c.id),
            link_id=str(c.id),
            link_kind=c.kind.value,
            seen_at=c.seen_at,
            technique=c.technique,
            source=c.source,
            note=mask(c.note),
        )
        if c.technique:
            edge("connection_technique", source, technique(c.technique), link_kind=c.kind.value)
            uses[c.technique]["connections"] += 1
            uses[c.technique]["first"].append(c.seen_at)

    techniques = []
    for tid in sorted(uses):
        use = uses[tid]
        name, tactics = attack.technique(tid)
        techniques.append(
            TechniqueUse(
                technique_id=tid,
                name=name,
                tactics=tactics,
                confirmed=use["confirmed"],
                leads=use["lead"],
                refuted=use["refuted"],
                connections=use["connections"],
                findings=use["findings"],
                first_seen=_earliest(*use["first"]),
            )
        )

    return GraphOut(
        incident_id=incident.id,
        ref=incident.ref,
        tlp=incident.tlp,
        restricted=notify.restricted(incident),
        masked=masked,
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        techniques=techniques,
        attack_version=attack.VERSION,
    )


async def incident_graph(db: AsyncSession, user: User, incident: Incident) -> GraphOut:
    """Read the incident's records and build the graph as `user` may see it."""
    of = incident.id

    async def rows(model, order):
        return list((await db.scalars(select(model).where(model.incident_id == of).order_by(order))).all())

    findings = await rows(Finding, Finding.seq)
    citations = (
        await db.execute(
            select(FindingEvidence.finding_id, FindingEvidence.evidence_id)
            .join(Finding, Finding.id == FindingEvidence.finding_id)
            .where(Finding.incident_id == of)
        )
    ).all()
    return build(
        incident,
        masked=hides_iocs(user),
        mask=lambda text: for_viewer(user, text),
        observables=await rows(IncidentObservable, IncidentObservable.added_at),
        assets=await rows(Asset, Asset.seq),
        connections=await rows(AssetLink, AssetLink.seen_at),
        findings=findings,
        citations=[(f, e) for f, e in citations],
        exhibits=await rows(EvidenceItem, EvidenceItem.seq),
    )
