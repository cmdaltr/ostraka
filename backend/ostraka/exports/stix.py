"""An incident as a STIX 2.1 bundle, written as plain JSON.

Hand-written rather than through the `stix2` library: the objects used here are few and fixed, the
output is checked in tests against the spec's rules (ids, references, timestamps), and the library
would be a large dependency for building dictionaries. Shikari's exporter (shikari/parsers/
stix_export.py) uses `stix2` and carries no markings; the shape here follows Rowanholt's reading of
bundles instead (rowanholt/threat_intel/stix.py): each indicator names its own techniques through
`indicates` relationships, never every technique in the bundle.

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

import ipaddress
import json
import ntpath
import posixpath
import uuid
from datetime import UTC, datetime
from typing import Any

from ostraka.exports.record import CONFIRMED, IntelFinding, IntelObservable, IntelRecord
from ostraka.models import Tlp
from ostraka.observables.detect import ObservableType as T
from ostraka.reports import attack

SPEC = "2.1"
MEDIA_TYPE = "application/stix+json;version=2.1"
# Ostraka's own namespace for the ids of its objects; the SCO namespace is the spec's (2.1, 2.9)
NAMESPACE = uuid.UUID("5c6f5e0e-58f4-4f39-9b0e-0f3e2a5c0b7d")
SCO_NAMESPACE = uuid.UUID("00abedb4-aa42-466c-9c01-fed23315a9b7")
CONFIDENCE = {CONFIRMED: 85, "lead": 15}
ATTACK_URL = "https://attack.mitre.org/techniques/"

# OASIS TLP 2.0 (cti-stix-common-objects, extension-definition-specifications/tlp-2.0)
TLP_EXTENSION = "extension-definition--60a3c5c5-0d10-413e-aab3-9e08dde9e88d"
TLP_MARKINGS = {
    Tlp.CLEAR: "marking-definition--94868c89-83c2-464b-929b-a1a8aa3c8487",
    Tlp.GREEN: "marking-definition--bab4a63c-aed9-4cf5-a766-dfca5abac2bb",
    Tlp.AMBER: "marking-definition--55d920b0-5e8b-4f79-9ee9-91f868d9b421",
    Tlp.AMBER_STRICT: "marking-definition--939a9414-2ddd-4d32-a0cd-375ea402b003",
    Tlp.RED: "marking-definition--e828b379-4e03-4974-9ac4-e53a884c97c1",
}
TLP_CREATED = "2022-10-01T00:00:00.000Z"


def timestamp(at: datetime) -> str:
    """A STIX timestamp: UTC, millisecond precision, Z."""
    at = at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def tlp_marking(tlp: Tlp) -> dict[str, Any]:
    return {
        "type": "marking-definition",
        "spec_version": SPEC,
        "id": TLP_MARKINGS[tlp],
        "created": TLP_CREATED,
        "name": f"TLP:{tlp.value.upper()}",
        "extensions": {TLP_EXTENSION: {"extension_type": "property-extension", "tlp_2_0": tlp.value}},
    }


def _id(kind: str, *parts: object) -> str:
    return f"{kind}--{uuid.uuid5(NAMESPACE, '|'.join(str(p) for p in parts))}"


def _sco_id(kind: str, contributing: dict[str, Any]) -> str:
    """The spec's deterministic id: UUIDv5 over the canonical JSON of the ID contributing properties."""
    canonical = json.dumps(contributing, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{kind}--{uuid.uuid5(SCO_NAMESPACE, canonical)}"


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


_HASH_NAMES = {T.FILE_HASH_MD5: "MD5", T.FILE_HASH_SHA1: "SHA-1", T.FILE_HASH_SHA256: "SHA-256"}
_CERT_HASHES = {32: "MD5", 40: "SHA-1", 64: "SHA-256"}


def _split_path(path: str) -> tuple[str, str]:
    """(directory, name) for a Windows or POSIX path."""
    module = ntpath if ("\\" in path or ":" in path[:3]) else posixpath
    head, tail = module.split(path)
    return head, tail


def cyber_observables(kind: T, value: str) -> tuple[list[dict[str, Any]], str | None]:
    """The STIX cyber observables for one value, and the pattern an indicator matches it with.
    ([], None) when STIX has no faithful way to say it (an alias, a phone number, "other")."""
    v = value.strip()

    def sco(stix_type: str, contributing: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {
            "type": stix_type,
            "spec_version": SPEC,
            "id": _sco_id(stix_type, contributing),
            **contributing,
            **extra,
        }

    if kind in (T.IP_ADDRESS, T.CIDR):
        try:
            version = ipaddress.ip_network(v, strict=False).version
        except ValueError:
            return [], None
        stix_type = "ipv6-addr" if version == 6 else "ipv4-addr"
        return [sco(stix_type, {"value": v})], f"[{stix_type}:value = {_quote(v)}]"
    simple = {
        T.DOMAIN: ("domain-name", "value"),
        T.URL: ("url", "value"),
        T.EMAIL: ("email-addr", "value"),
        T.MUTEX: ("mutex", "name"),
        T.REGISTRY_KEY: ("windows-registry-key", "key"),
        T.MAC_ADDRESS: ("mac-addr", "value"),
        T.USER_ACCOUNT: ("user-account", "account_login"),
    }
    if kind in simple:
        stix_type, prop = simple[kind]
        if kind is T.MAC_ADDRESS:
            v = v.lower().replace("-", ":")
        return [sco(stix_type, {prop: v})], f"[{stix_type}:{prop} = {_quote(v)}]"
    if kind in _HASH_NAMES:
        name = _HASH_NAMES[kind]
        return [sco("file", {"hashes": {name: v.lower()}})], f"[file:hashes.'{name}' = {_quote(v.lower())}]"
    if kind is T.FILE_NAME:
        return [sco("file", {"name": v})], f"[file:name = {_quote(v)}]"
    if kind is T.FILE_PATH:
        directory, name = _split_path(v)
        if not name:
            return [sco("directory", {"path": v})], f"[directory:path = {_quote(v)}]"
        if not directory:
            return [sco("file", {"name": name})], f"[file:name = {_quote(name)}]"
        folder = sco("directory", {"path": directory})
        file = sco("file", {"name": name, "parent_directory_ref": folder["id"]})
        pattern = f"[file:name = {_quote(name)} AND file:parent_directory_ref.path = {_quote(directory)}]"
        return [file, folder], pattern
    if kind is T.PROCESS_NAME:
        return [sco("file", {"name": v})], f"[process:image_ref.name = {_quote(v)}]"
    if kind is T.PROCESS_COMMAND:
        # process has no ID contributing properties; Ostraka's namespace keeps the id stable
        obj = {"type": "process", "spec_version": SPEC, "id": _id("process", v), "command_line": v}
        return [obj], f"[process:command_line = {_quote(v)}]"
    if kind is T.EMAIL_SUBJECT:
        return [
            sco("email-message", {"subject": v}, is_multipart=False)
        ], f"[email-message:subject = {_quote(v)}]"
    if kind is T.ASN:
        digits = v.upper().removeprefix("AS")
        if not digits.isdigit():
            return [], None
        return [
            sco("autonomous-system", {"number": int(digits)})
        ], f"[autonomous-system:number = {int(digits)}]"
    if kind is T.CERTIFICATE:
        name = _CERT_HASHES.get(len(v)) if all(c in "0123456789abcdefABCDEF:" for c in v) else None
        if name:
            h = v.replace(":", "").lower()
            return [
                sco("x509-certificate", {"hashes": {name: h}})
            ], f"[x509-certificate:hashes.'{name}' = {_quote(h)}]"
        return [
            sco("x509-certificate", {"serial_number": v})
        ], f"[x509-certificate:serial_number = {_quote(v)}]"
    if kind is T.SERVICE_NAME:
        return [], f"[process:extensions.'windows-service-ext'.service_name = {_quote(v)}]"
    if kind is T.USER_AGENT:
        return (
            [],
            f"[network-traffic:extensions.'http-request-ext'.request_header.'User-Agent' = {_quote(v)}]",
        )
    return [], None


def _common(record: IntelRecord, created: datetime, now: datetime, identity: str) -> dict[str, Any]:
    created_ts = timestamp(min(created, now))
    return {
        "spec_version": SPEC,
        "created_by_ref": identity,
        "created": created_ts,
        "modified": timestamp(now),
        "object_marking_refs": [TLP_MARKINGS[record.tlp]],
    }


def _standing(standing: str) -> dict[str, Any]:
    return {"labels": [standing], "confidence": CONFIDENCE[standing]}


def _attack_pattern(record: IntelRecord, tid: str, now: datetime, identity: str) -> dict[str, Any]:
    name, tactics = attack.technique(tid)
    return {
        "type": "attack-pattern",
        "id": _id("attack-pattern", "mitre-attack", tid),
        **_common(record, record.declared_at, now, identity),
        "name": name or tid,
        "external_references": [
            {
                "source_name": "mitre-attack",
                "external_id": tid,
                "url": f"{ATTACK_URL}{tid.replace('.', '/')}/",
            }
        ],
        **(
            {"kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": t} for t in tactics]}
            if tactics
            else {}
        ),
    }


def _indicator(
    record: IntelRecord, item: IntelObservable, pattern: str, now: datetime, identity: str
) -> dict[str, Any]:
    word = "Confirmed" if item.confirmed else "Lead, not confirmed"
    description = f"{word}. Verdict: {item.verdict.value}."
    if item.cited_by:
        description += f" Cited by {', '.join(item.cited_by)}."
    if item.note:
        description += f" {item.note}"
    return {
        "type": "indicator",
        "id": _id("indicator", record.id, item.id),
        **_common(record, item.added_at, now, identity),
        "name": f"{item.ref} ({item.kind.value.replace('_', ' ')})",
        "description": description,
        "indicator_types": ["malicious-activity" if item.confirmed else "anomalous-activity"],
        "pattern": pattern,
        "pattern_type": "stix",
        "pattern_version": SPEC,
        "valid_from": timestamp(item.first_seen),
        **_standing(item.standing),
        "external_references": [{"source_name": "ostraka", "external_id": item.ref}],
    }


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
        {
            "type": "identity",
            "spec_version": SPEC,
            "id": identity,
            "created": timestamp(min(record.declared_at, now)),
            "modified": timestamp(now),
            "name": record.producer,
            "identity_class": "organization",
        },
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
                objects.append(
                    {
                        "type": "relationship",
                        "id": _id("relationship", record.id, item.id, finding.id, tid),
                        **_common(record, finding.created_at, now, identity),
                        "relationship_type": "indicates",
                        "source_ref": indicator["id"],
                        "target_ref": patterns[tid],
                        "description": f"Through {finding.ref}.",
                        **_standing(finding.standing if item.confirmed else "lead"),
                    }
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
