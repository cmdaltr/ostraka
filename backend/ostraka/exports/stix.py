"""An incident as a STIX 2.1 bundle, written as plain JSON.

The objects samarkand knows (identity, the TLP marking, attack-pattern, indicator, relationship and
the cyber observables, with their ids and timestamps) are built by `samarkand.stix`, shared with
Shikari and Rowanholt, under Ostraka's own NAMESPACE. What only an incident has (the incident, its
notes, sightings and observed data, and the report) is built here, with the same `common` properties.
Each indicator names its own techniques through `indicates` relationships, never every technique in
the bundle.

What's in a bundle:
    identity           who produced it: the licensee, or Ostraka
    marking-definition the incident's TLP, as the OASIS TLP 2.0 extension defines it; every other
                       object carries it in object_marking_refs
    incident           the incident (STIX 2.1's stub incident: name and description)
    report             the export, referencing everything else, linked back to the incident in Ostraka
    attack-pattern     each ATT&CK technique a finding names, by its ATT&CK ID
    indicator          each observable that isn't benign, with a STIX pattern; confirmed or lead
    <cyber observable> the observable itself (ipv4-addr, domain-name, file and so on), where STIX has one
    vulnerability      each CVE among the observables
    observed-data      one per sighting, with its sighting relationship to the indicator
    note               each finding that isn't refuted, with the indicators and techniques it covers
    relationship       indicator -indicates-> attack-pattern, when a finding citing the observable
                       names the technique

Confirmed and lead are kept apart three ways: the labels ("confirmed" or "lead"), `confidence` on
STIX 2.1's None/Low/Med/High scale (Appendix A: High 85, Low 15), and an indicator's
indicator_types ("malicious-activity" only when confirmed, "anomalous-activity" otherwise).

Ids are stable: the same incident exported twice gives the same ids, so a platform that imports
both sees the second as a newer version (`modified`). Cyber observables use the spec's own
deterministic ids (UUIDv5 over their ID contributing properties).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from samarkand import stix as shared
from samarkand.stix import (  # noqa: F401  (re-exported for the API and tests)
    MEDIA_TYPE,
    SPEC,
    TLP_EXTENSION,
    TLP_MARKINGS,
    timestamp,
    tlp_marking,
)

from ostraka.exports.record import CONFIRMED, IntelFinding, IntelObservable, IntelRecord
from ostraka.observables.detect import ObservableType as T
from ostraka.reports import attack

# Ostraka's own namespace for the ids of its objects; the SCO namespace is the spec's (2.1, 2.9)
NAMESPACE = uuid.UUID("5c6f5e0e-58f4-4f39-9b0e-0f3e2a5c0b7d")
CONFIDENCE = {CONFIRMED: 85, "lead": 15}


def _id(kind: str, *parts: object) -> str:
    return shared.object_id(NAMESPACE, kind, *parts)


def cyber_observables(kind: T, value: str) -> tuple[list[dict[str, Any]], str | None]:
    """The STIX cyber observables for one value, and the pattern an indicator matches it with.
    ([], None) when STIX has no faithful way to say it (an alias, a phone number, "other")."""
    return shared.cyber_observables(kind, value, namespace=NAMESPACE)


def _common(record: IntelRecord, created: datetime, now: datetime, identity: str) -> dict[str, Any]:
    return shared.common(identity, created, now, record.tlp)


def _standing(standing: str) -> dict[str, Any]:
    return {"labels": [standing], "confidence": CONFIDENCE[standing]}


def _attack_pattern(record: IntelRecord, tid: str, now: datetime, identity: str) -> dict[str, Any]:
    name, tactics = attack.technique(tid)
    props = _common(record, record.declared_at, now, identity)
    return shared.attack_pattern_object(_id("attack-pattern", "mitre-attack", tid), tid, props, name, tactics)


def _indicator(
    record: IntelRecord, item: IntelObservable, pattern: str, now: datetime, identity: str
) -> dict[str, Any]:
    word = "Confirmed" if item.confirmed else "Lead, not confirmed"
    description = f"{word}. Verdict: {item.verdict.value}."
    if item.cited_by:
        description += f" Cited by {', '.join(item.cited_by)}."
    if item.note:
        description += f" {item.note}"
    return shared.indicator_object(
        _id("indicator", record.id, item.id),
        _common(record, item.added_at, now, identity),
        name=f"{item.ref} ({item.kind.value.replace('_', ' ')})",
        description=description,
        indicator_types=["malicious-activity" if item.confirmed else "anomalous-activity"],
        pattern=pattern,
        valid_from=item.first_seen,
        labels=[item.standing],
        confidence=CONFIDENCE[item.standing],
        external_references=[{"source_name": "ostraka", "external_id": item.ref}],
    )


def _note(
    record: IntelRecord, finding: IntelFinding, refs: list[str], now: datetime, identity: str
) -> dict[str, Any]:
    word = "Confirmed finding" if finding.standing == CONFIRMED else "Lead, not confirmed"
    return {
        "type": "note",
        "id": _id("note", record.id, finding.id),
        **_common(record, finding.created_at, now, identity),
        "abstract": f"{finding.ref}: {finding.title}",
        "content": f"{word} ({finding.kind.value}). {finding.statement}".strip(),
        "object_refs": refs,
        **_standing(finding.standing),
        "external_references": [{"source_name": "ostraka", "external_id": finding.ref, "url": finding.url}],
    }


def bundle(record: IntelRecord, now: datetime) -> dict[str, Any]:
    """The incident as a STIX 2.1 bundle (a dict; `json.dumps` it to send)."""
    identity = _id("identity", record.producer_key or record.producer)
    objects: list[dict[str, Any]] = [
        shared.identity_object(identity, record.producer, record.declared_at, now),
        tlp_marking(record.tlp),
    ]
    incident_id = _id("incident", record.id)
    objects.append(
        {
            "type": "incident",
            "id": incident_id,
            **_common(record, record.declared_at, now, identity),
            "name": f"{record.ref}: {record.title}",
            **({"description": record.summary} if record.summary else {}),
            "labels": [f"severity-{record.severity.value}", record.kind.value],
            "external_references": [{"source_name": "ostraka", "external_id": record.ref, "url": record.url}],
        }
    )

    patterns: dict[str, str] = {}
    for tid in record.techniques():
        ap = _attack_pattern(record, tid, now, identity)
        patterns[tid] = ap["id"]
        objects.append(ap)
    for item in record.observables:
        if item.kind is T.MITRE_TECHNIQUE:
            tid = item.value.strip().upper()
            if tid not in patterns:
                ap = _attack_pattern(record, tid, now, identity)
                patterns[tid] = ap["id"]
                objects.append(ap)

    indicators: dict[uuid.UUID, str] = {}
    findings = {f.ref: f for f in record.findings}
    for item in record.observables:
        if item.kind is T.MITRE_TECHNIQUE:
            continue
        if item.kind is T.CVE:
            cve = item.value.strip().upper()
            vulnerability = {
                "type": "vulnerability",
                "id": _id("vulnerability", cve),
                **_common(record, item.added_at, now, identity),
                "name": cve,
                "external_references": [{"source_name": "cve", "external_id": cve}],
                **_standing(item.standing),
            }
            indicators[item.id] = vulnerability["id"]
            objects.append(vulnerability)
            continue
        scos, pattern = cyber_observables(item.kind, item.value)
        if pattern is None:
            continue
        for obj in scos:
            if not any(o["id"] == obj["id"] for o in objects):
                objects.append({**obj, "object_marking_refs": [TLP_MARKINGS[record.tlp]]})
        indicator = _indicator(record, item, pattern, now, identity)
        indicators[item.id] = indicator["id"]
        objects.append(indicator)

        for sighting in item.sightings:
            seen = timestamp(sighting.seen_at)
            observed_refs = []
            if scos:
                observed = {
                    "type": "observed-data",
                    "id": _id("observed-data", record.id, sighting.id),
                    **_common(record, sighting.seen_at, now, identity),
                    "first_observed": seen,
                    "last_observed": seen,
                    "number_observed": 1,
                    "object_refs": [o["id"] for o in scos],
                    **_standing(item.standing),
                }
                objects.append(observed)
                observed_refs = [observed["id"]]
            where = ", ".join(
                part
                for part in (
                    f"host {sighting.host}" if sighting.host else "",
                    f"account {sighting.user_account}" if sighting.user_account else "",
                    f"source {sighting.source}" if sighting.source else "",
                )
                if part
            )
            objects.append(
                {
                    "type": "sighting",
                    "id": _id("sighting", record.id, sighting.id),
                    **_common(record, sighting.seen_at, now, identity),
                    "sighting_of_ref": indicator["id"],
                    **({"observed_data_refs": observed_refs} if observed_refs else {}),
                    "where_sighted_refs": [identity],
                    "first_seen": seen,
                    "last_seen": seen,
                    "count": 1,
                    **({"description": f"Seen: {where}."} if where else {}),
                    **_standing(item.standing),
                }
            )

        # A technique is indicated by an observable only through a finding that cites both
        for ref in item.cited_by:
            finding = findings.get(ref)
            for tid in finding.techniques if finding else ():
                standing = finding.standing if item.confirmed else "lead"
                objects.append(
                    shared.relationship_object(
                        _id("relationship", record.id, item.id, finding.id, tid),
                        _common(record, finding.created_at, now, identity),
                        "indicates",
                        indicator["id"],
                        patterns[tid],
                        description=f"Through {finding.ref}.",
                        labels=[standing],
                        confidence=CONFIDENCE[standing],
                    )
                )

    for finding in record.findings:
        refs = [incident_id]
        refs += [indicators[o] for o in finding.observable_ids if o in indicators]
        refs += [patterns[t] for t in finding.techniques]
        objects.append(_note(record, finding, refs, now, identity))

    report_refs = [o["id"] for o in objects if o["type"] not in ("identity", "marking-definition")]
    objects.append(
        {
            "type": "report",
            "id": _id("report", record.id),
            **_common(record, record.declared_at, now, identity),
            "name": f"{record.ref}: {record.title}",
            "description": (
                f"Exported from Ostraka. TLP:{record.tlp.value.upper()}. "
                "Labels and confidence say whether each object is a confirmed finding or a lead."
            ),
            "report_types": ["threat-report"],
            "published": timestamp(now),
            "object_refs": report_refs,
            "external_references": [{"source_name": "ostraka", "external_id": record.ref, "url": record.url}],
        }
    )
    return {"type": "bundle", "id": _id("bundle", record.id, timestamp(now)), "objects": objects}
