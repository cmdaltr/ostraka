"""STIX, MISP and Navigator exports without a database: who may export, and each format checked
against its own rules."""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from ostraka.auth import policy
from ostraka.exports import misp, navigator, stix
from ostraka.exports.record import (
    IntelFinding,
    IntelObservable,
    IntelRecord,
    IntelSighting,
    is_confirmed_indicator,
)
from ostraka.models import FindingKind, FindingStatus, RecordKind, Role, Severity, Tlp, User, Verdict
from ostraka.observables.detect import ObservableType as T

NOW = datetime(2026, 11, 11, 12, 0, 0, 123456, tzinfo=UTC)
T0 = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)


def person(role: Role) -> User:
    return User(id=uuid.uuid4(), role=role, email="x@example.org", name="X")


# ---- policy ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "worked_on", "is_commander", "sensitive", "restricted", "push", "allowed"),
    [
        (Role.RESPONDER, True, False, False, False, False, True),  # as for reports
        (Role.RESPONDER, True, False, False, False, True, True),  # and may push
        (Role.RESPONDER, False, False, False, False, False, False),  # never worked on it
        (Role.OBSERVER, True, False, False, False, False, False),
        (Role.RESPONDER, True, False, True, False, False, False),  # sensitive: commander only
        (Role.RESPONDER, False, True, True, False, False, True),
        (Role.RESPONDER, True, False, False, True, False, False),  # TLP:RED, not the commander
        (Role.RESPONDER, False, True, False, True, False, True),  # TLP:RED, the commander downloads
        (Role.RESPONDER, False, True, False, True, True, False),  # but never pushes
        (Role.ADMIN, False, True, False, True, True, False),  # whoever asks
    ],
)
def test_who_may_export_intel_and_restricted_tlp_is_never_pushed(
    role, worked_on, is_commander, sensitive, restricted, push, allowed
):
    why = policy.can_export_intel(
        person(role),
        worked_on=worked_on,
        is_commander=is_commander,
        sensitive=sensitive,
        restricted=restricted,
        push=push,
    )
    assert (why is None) is allowed, why


def test_only_admins_set_up_misp():
    assert policy.can_manage_misp(person(Role.ADMIN)) is None
    for role in (Role.TEAM_LEAD, Role.RESPONDER, Role.OBSERVER):
        assert policy.can_manage_misp(person(role))


@pytest.mark.parametrize(
    ("verdict", "citing", "confirmed"),
    [
        (Verdict.MALICIOUS, [FindingStatus.CONFIRMED], True),
        (Verdict.MALICIOUS, [FindingStatus.LEAD], False),  # a lead's indicator stays a lead
        (Verdict.MALICIOUS, [], False),  # a verdict alone isn't a verified finding
        (Verdict.SUSPICIOUS, [FindingStatus.CONFIRMED], False),
    ],
)
def test_an_indicator_is_confirmed_only_through_a_confirmed_finding(verdict, citing, confirmed):
    assert is_confirmed_indicator(verdict, citing) is confirmed


# ---- a record to export -----------------------------------------------------------------------------


def record(tlp: Tlp = Tlp.AMBER) -> IntelRecord:
    ip, url, hash_, path, cmd, cve = (uuid.uuid4() for _ in range(6))
    confirmed = IntelFinding(
        id=uuid.uuid4(),
        ref="FND-12",
        kind=FindingKind.EVIDENCE,
        title="Beacon to the C2 address from FIN-WS-07",
        statement="Based on the proxy logs, we determine the host beaconed.",
        status=FindingStatus.CONFIRMED,
        techniques=("T1071.001",),
        observable_ids=(ip, hash_),
        created_at=T0,
        decided_at=T0 + timedelta(hours=2),
        url="http://ostraka.test/incidents/OST-2026-014?s=findings#FND-12",
    )
    lead = IntelFinding(
        id=uuid.uuid4(),
        ref="FND-13",
        kind=FindingKind.POTENTIAL,
        title="Possible exfiltration over the same channel",
        statement="",
        status=FindingStatus.LEAD,
        techniques=("T1041", "T1071.001"),
        observable_ids=(url, ip),
        created_at=T0 + timedelta(hours=1),
        url="http://ostraka.test/incidents/OST-2026-014?s=findings#FND-13",
    )
    seen = IntelSighting(id=uuid.uuid4(), seen_at=T0, host="FIN-WS-07", source="Defender XDR")
    return IntelRecord(
        id=uuid.uuid4(),
        ref="OST-2026-014",
        kind=RecordKind.INCIDENT,
        title="Cobalt Strike on the finance floor",
        summary="A beacon on one workstation.",
        severity=Severity.HIGH,
        tlp=tlp,
        declared_at=T0,
        updated_at=T0,
        closed_at=None,
        url="http://ostraka.test/incidents/OST-2026-014",
        producer="Northwind Ltd",
        producer_key="http://ostraka.test/|Northwind Ltd",
        findings=(confirmed, lead),
        observables=(
            IntelObservable(
                id=ip,
                ref="OBS-1",
                kind=T.IP_ADDRESS,
                value="203.0.113.45",
                verdict=Verdict.MALICIOUS,
                added_at=T0,
                sightings=(seen,),
                cited_by=("FND-12", "FND-13"),
                confirmed=True,
            ),
            IntelObservable(
                id=hash_,
                ref="OBS-2",
                kind=T.FILE_HASH_SHA256,
                value="9D3C7A1E5B2F48C06E1A7D4B9F3C2E8A1D6B5F4C3A2E1D0F9B8C7A6E5D4C3B2A",
                verdict=Verdict.MALICIOUS,
                added_at=T0,
                cited_by=("FND-12",),
                confirmed=True,
            ),
            IntelObservable(
                id=url,
                ref="OBS-3",
                kind=T.URL,
                value="https://evil.example/it's/gate.php",
                verdict=Verdict.SUSPICIOUS,
                added_at=T0,
                cited_by=("FND-13",),
            ),
            IntelObservable(
                id=path,
                ref="OBS-4",
                kind=T.FILE_PATH,
                value="C:\\Users\\Public\\beacon.exe",
                verdict=Verdict.UNKNOWN,
                added_at=T0,
            ),
            IntelObservable(
                id=cmd,
                ref="OBS-5",
                kind=T.PROCESS_COMMAND,
                value="powershell -nop -w hidden -enc SQBFAFgA",
                verdict=Verdict.SUSPICIOUS,
                added_at=T0,
            ),
            IntelObservable(
                id=cve, ref="OBS-6", kind=T.CVE, value="cve-2024-3400", verdict=Verdict.UNKNOWN, added_at=T0
            ),
            IntelObservable(
                id=uuid.uuid4(),
                ref="OBS-7",
                kind=T.ALIAS,
                value="Big Bad",
                verdict=Verdict.UNKNOWN,
                added_at=T0,
            ),
        ),
    )


# ---- STIX -------------------------------------------------------------------------------------------

ID = re.compile(
    r"^([a-z][a-z0-9-]+)--[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
SCO = {
    "ipv4-addr", "ipv6-addr", "domain-name", "url", "email-addr", "file", "directory", "process", "mutex",
    "windows-registry-key", "mac-addr", "user-account", "email-message", "autonomous-system",
    "x509-certificate",
}  # fmt: skip
SRO = {"relationship", "sighting"}
REQUIRED = {
    "identity": {"name"},
    "incident": {"name"},
    "report": {"name", "published", "object_refs"},
    "attack-pattern": {"name"},
    "indicator": {"pattern", "pattern_type", "valid_from"},
    "observed-data": {"first_observed", "last_observed", "number_observed", "object_refs"},
    "note": {"content", "object_refs"},
    "vulnerability": {"name"},
    "relationship": {"relationship_type", "source_ref", "target_ref"},
    "sighting": {"sighting_of_ref"},
    "marking-definition": {"created"},
}


def refs_in(obj, key=""):
    """Every identifier an object refers to, through *_ref and *_refs properties at any depth."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k != "id":
                yield from refs_in(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from refs_in(v, key)
    elif isinstance(obj, str) and (key.endswith("_ref") or key.endswith("_refs")):
        yield obj


def check_bundle(bundle: dict) -> dict[str, dict]:
    """The rules of STIX 2.1 a consumer relies on, checked over every object."""
    assert bundle["type"] == "bundle" and ID.match(bundle["id"]).group(1) == "bundle"
    assert "spec_version" not in bundle  # a 2.1 bundle has none
    objects = {o["id"]: o for o in bundle["objects"]}
    assert len(objects) == len(bundle["objects"]), "duplicate ids"
    for obj in bundle["objects"]:
        match = ID.match(obj["id"])
        assert match and match.group(1) == obj["type"], obj["id"]
        assert obj["spec_version"] == "2.1"
        assert REQUIRED.get(obj["type"], set()) <= obj.keys(), obj["type"]
        if obj["type"] not in SCO:
            assert TS.match(obj["created"]), obj
            if obj["type"] != "marking-definition":
                assert TS.match(obj["modified"]) and obj["modified"] >= obj["created"], obj
        for key in ("valid_from", "published", "first_seen", "last_seen", "first_observed", "last_observed"):
            if key in obj:
                assert TS.match(obj[key]), (key, obj)
        if "first_seen" in obj:
            assert obj["last_seen"] >= obj["first_seen"]
        if "confidence" in obj:
            assert 0 <= obj["confidence"] <= 100
        for ref in refs_in(obj):
            assert ref in objects, f"{obj['id']} refers to missing {ref}"
        if obj["type"] == "relationship":
            assert objects[obj["source_ref"]]["type"] == "indicator"
            assert objects[obj["target_ref"]]["type"] == "attack-pattern"
        if obj["type"] == "sighting":
            assert objects[obj["sighting_of_ref"]]["type"] == "indicator"
            assert all(objects[r]["type"] == "identity" for r in obj["where_sighted_refs"])
        if obj["type"] == "indicator":
            pattern = obj["pattern"]
            assert (
                pattern.startswith("[") and pattern.endswith("]") and pattern.count("[") == pattern.count("]")
            )
    return objects


def test_a_bundle_follows_the_spec_and_every_reference_resolves():
    data = stix.bundle(record(), NOW)
    json.dumps(data)  # serialisable as it stands
    objects = check_bundle(data)
    types = sorted({o["type"] for o in objects.values()})
    assert types == sorted(
        [
            "identity", "marking-definition", "incident", "report", "attack-pattern", "indicator",
            "ipv4-addr", "file", "url", "directory", "process", "vulnerability", "observed-data", "sighting",
            "note", "relationship",
        ]
    )  # fmt: skip
    report = next(o for o in objects.values() if o["type"] == "report")
    assert {
        o["id"] for o in objects.values() if o["type"] not in ("identity", "marking-definition", "report")
    } == set(report["object_refs"])
    assert report["external_references"][0] == {
        "source_name": "ostraka",
        "external_id": "OST-2026-014",
        "url": "http://ostraka.test/incidents/OST-2026-014",
    }
    # An alias has no STIX form and is left out rather than forced into one
    assert not any("OBS-7" in json.dumps(o) for o in objects.values())


def test_the_tlp_marking_is_the_oasis_tlp_2_definition_on_every_object():
    for tlp in Tlp:
        objects = check_bundle(stix.bundle(record(tlp), NOW))
        marking = objects[stix.TLP_MARKINGS[tlp]]
        assert marking["name"] == f"TLP:{tlp.value.upper()}"
        assert marking["extensions"][stix.TLP_EXTENSION] == {
            "extension_type": "property-extension",
            "tlp_2_0": tlp.value,
        }
        for obj in objects.values():
            if obj["type"] not in ("marking-definition", "identity"):
                assert obj["object_marking_refs"] == [marking["id"]], obj["id"]
    assert stix.TLP_MARKINGS[Tlp.AMBER_STRICT] == "marking-definition--939a9414-2ddd-4d32-a0cd-375ea402b003"


def test_leads_are_never_presented_as_confirmed_in_stix():
    objects = check_bundle(stix.bundle(record(), NOW)).values()
    by_name = {o.get("name"): o for o in objects if o["type"] == "indicator"}
    ip, url = by_name["OBS-1 (ip address)"], by_name["OBS-3 (url)"]
    assert ip["labels"] == ["confirmed"] and ip["confidence"] == 85
    assert ip["indicator_types"] == ["malicious-activity"]
    assert url["labels"] == ["lead"] and url["confidence"] == 15
    assert url["indicator_types"] == ["anomalous-activity"] and "Lead, not confirmed" in url["description"]

    notes = {o["abstract"].split(":")[0]: o for o in objects if o["type"] == "note"}
    assert notes["FND-12"]["labels"] == ["confirmed"] and notes["FND-12"]["content"].startswith("Confirmed")
    assert notes["FND-13"]["labels"] == ["lead"] and notes["FND-13"]["content"].startswith(
        "Lead, not confirmed"
    )

    # The confirmed IP indicates T1071.001 through FND-12 (confirmed), and T1041 and T1071.001 through
    # FND-13, which is still a lead
    rels = [o for o in objects if o["type"] == "relationship"]
    names = {
        o["id"]: o["external_references"][0]["external_id"] for o in objects if o["type"] == "attack-pattern"
    }
    assert sorted(
        (r["description"], names[r["target_ref"]], r["labels"][0])
        for r in rels
        if r["source_ref"] == ip["id"]
    ) == [
        ("Through FND-12.", "T1071.001", "confirmed"),
        ("Through FND-13.", "T1041", "lead"),
        ("Through FND-13.", "T1071.001", "lead"),
    ]
    # The lead URL's relationships are leads whatever finding they come through
    assert {r["labels"][0] for r in rels if r["source_ref"] == url["id"]} == {"lead"}


def test_patterns_escape_values_and_observables_get_the_specs_ids():
    objects = check_bundle(stix.bundle(record(), NOW)).values()
    patterns = {o["name"]: o["pattern"] for o in objects if o["type"] == "indicator"}
    assert patterns["OBS-3 (url)"] == "[url:value = 'https://evil.example/it\\'s/gate.php']"
    assert patterns["OBS-2 (file hash sha256)"].startswith("[file:hashes.'SHA-256' = '9d3c7a1e")
    assert patterns["OBS-4 (file path)"] == (
        "[file:name = 'beacon.exe' AND file:parent_directory_ref.path = 'C:\\\\Users\\\\Public']"
    )
    # STIX 2.1, 2.9: UUIDv5 in the spec's namespace over the canonical JSON of the contributing properties
    namespace = uuid.UUID("00abedb4-aa42-466c-9c01-fed23315a9b7")
    scos, _ = stix.cyber_observables(T.IP_ADDRESS, "198.51.100.3")
    assert scos[0]["id"] == f"ipv4-addr--{uuid.uuid5(namespace, '{"value":"198.51.100.3"}')}"
    files, _ = stix.cyber_observables(T.FILE_HASH_MD5, "D41D8CD98F00B204E9800998ECF8427E")
    assert files[0]["hashes"] == {"MD5": "d41d8cd98f00b204e9800998ecf8427e"}
    canonical = '{"hashes":{"MD5":"d41d8cd98f00b204e9800998ecf8427e"}}'
    assert files[0]["id"] == f"file--{uuid.uuid5(namespace, canonical)}"


def test_the_same_incident_exports_with_the_same_ids():
    rec = record()
    first, later = stix.bundle(rec, NOW), stix.bundle(rec, NOW + timedelta(days=1))
    assert [o["id"] for o in first["objects"]] == [o["id"] for o in later["objects"]]
    assert first["id"] != later["id"]  # each bundle is its own


def test_timestamps_are_utc_milliseconds():
    assert stix.timestamp(NOW) == "2026-11-11T12:00:00.123Z"
    assert stix.timestamp(datetime(2026, 1, 1, 13, 0, tzinfo=UTC).astimezone()) == "2026-01-01T13:00:00.000Z"


# ---- MISP -------------------------------------------------------------------------------------------


def test_a_misp_event_maps_types_and_flags_only_confirmed_indicators_for_ids():
    event = misp.event(record(), now=NOW)["Event"]
    attrs = {
        a["comment"].split(".")[0]: a for a in event["Attribute"] if a["type"] not in ("comment", "link")
    }
    assert attrs["OBS-1"]["type"] == "ip-dst" and attrs["OBS-1"]["to_ids"] is True
    assert attrs["OBS-2"]["type"] == "sha256" and attrs["OBS-2"]["to_ids"] is True
    assert attrs["OBS-3"]["type"] == "url" and attrs["OBS-3"]["to_ids"] is False  # a lead
    assert attrs["OBS-5"]["type"] == "text" and attrs["OBS-5"]["to_ids"] is False
    assert attrs["OBS-6"] == {**attrs["OBS-6"], "type": "vulnerability", "value": "CVE-2024-3400"}
    assert "OBS-7" not in attrs  # no faithful MISP type
    confidence = misp.CONFIDENCE_TAG.format
    assert attrs["OBS-1"]["Tag"] == [{"name": confidence("high")}]
    assert attrs["OBS-3"]["Tag"] == [{"name": confidence("low")}]
    assert "Lead, not confirmed" in attrs["OBS-3"]["comment"]
    assert attrs["OBS-1"]["first_seen"] == T0.isoformat()


def test_misp_galaxies_carry_confirmed_techniques_only_and_the_tlp_tag():
    event = misp.event(record(Tlp.GREEN), now=NOW)["Event"]
    tags = [t["name"] for t in event["Tag"]]
    assert tags[0] == "tlp:green"
    # T1071.001 is confirmed through FND-12; T1041 only by the lead FND-13
    assert 'misp-galaxy:mitre-attack-pattern="Web Protocols - T1071.001"' in tags
    assert not any("T1041" in t for t in tags)
    clusters = event["Galaxy"][0]["GalaxyCluster"]
    assert [c["meta"]["external_id"] for c in clusters] == [["T1071.001"]]
    lead = next(a for a in event["Attribute"] if a["type"] == "comment" and a["value"].startswith("FND-13"))
    assert "Lead, not confirmed" in lead["value"] and "T1041" in lead["value"] and lead["to_ids"] is False
    assert event["published"] is False and event["distribution"] == "0" and event["threat_level_id"] == "1"


def test_a_misp_event_keeps_its_uuid_so_a_second_push_updates_it():
    rec = record()
    assert misp.event(rec)["Event"]["uuid"] == misp.event(rec, now=NOW)["Event"]["uuid"]
    uuids = [a["uuid"] for a in misp.event(rec)["Event"]["Attribute"]]
    assert len(set(uuids)) == len(uuids)


async def test_pushing_adds_the_first_time_and_updates_after(monkeypatch):
    calls = []
    known: set[str] = set()

    async def fetch(method, url, *, key, verify, body=None):
        calls.append((method, url, key, verify))
        if method == "GET":
            return (200 if url.rsplit("/", 1)[1] in known else 404), b"{}"
        payload = json.loads(body)
        known.add(payload["Event"]["uuid"])
        return 200, json.dumps({"Event": {"id": "41", "uuid": payload["Event"]["uuid"]}}).encode()

    monkeypatch.setattr(misp, "fetch", fetch)
    payload = misp.event(record())
    first = await misp.push("https://misp.example/", "k3y", True, payload)
    again = await misp.push("https://misp.example", "k3y", True, payload)
    assert (first.action, again.action) == ("added", "updated")
    assert first.url == "https://misp.example/events/view/41"
    assert [c[:2] for c in calls] == [
        ("GET", f"https://misp.example/events/view/{payload['Event']['uuid']}"),
        ("POST", "https://misp.example/events/add"),
        ("GET", f"https://misp.example/events/view/{payload['Event']['uuid']}"),
        ("POST", f"https://misp.example/events/edit/{payload['Event']['uuid']}"),
    ]
    assert all(c[2] == "k3y" and c[3] is True for c in calls)


async def test_a_refused_push_says_why(monkeypatch):
    async def fetch(method, url, *, key, verify, body=None):
        return (404, b"") if method == "GET" else (403, b'{"message": "Authentication failed."}')

    monkeypatch.setattr(misp, "fetch", fetch)
    with pytest.raises(misp.MispProblem, match="refused the API key"):
        await misp.push("https://misp.example", "bad", True, misp.event(record()))


# ---- Navigator --------------------------------------------------------------------------------------


def test_the_layer_matches_rowanholts_and_holds_confirmed_techniques_only():
    data = navigator.layer(record())
    assert data["versions"] == {"attack": navigator.attack_major(), "navigator": "5.3.2", "layer": "4.5"}
    assert data["versions"]["attack"] == "20" and data["domain"] == "enterprise-attack"
    assert "IaaS" in data["filters"]["platforms"] and data["layout"]["showID"] is True
    assert [t["techniqueID"] for t in data["techniques"]] == ["T1071.001"]
    technique = data["techniques"][0]
    assert technique["color"] == navigator.CONFIRMED_COLOUR
    assert technique["comment"] == "FND-12 Beacon to the C2 address from FIN-WS-07 (confirmed)"
    assert technique["links"] == [
        {"label": "FND-12", "url": "http://ostraka.test/incidents/OST-2026-014?s=findings#FND-12"}
    ]
    assert data["legendItems"] == [{"label": "Confirmed finding", "color": navigator.CONFIRMED_COLOUR}]
    assert {"name": "TLP", "value": "TLP:AMBER"} in data["metadata"]


def test_with_leads_the_layer_shows_them_in_their_own_colour():
    data = navigator.layer(record(), leads=True)
    colours = {t["techniqueID"]: t["color"] for t in data["techniques"]}
    assert colours == {"T1041": navigator.LEAD_COLOUR, "T1071.001": navigator.CONFIRMED_COLOUR}
    exfil = next(t for t in data["techniques"] if t["techniqueID"] == "T1041")
    assert exfil["comment"].endswith("(lead)")
    both = next(t for t in data["techniques"] if t["techniqueID"] == "T1071.001")
    assert (
        both["comment"].startswith("FND-12")
        and "FND-13 Possible exfiltration over the same channel (lead)" in (both["comment"])
    )
    assert {"label": "Lead, not confirmed", "color": navigator.LEAD_COLOUR} in data["legendItems"]
    assert data["name"] == "OST-2026-014 (confirmed and leads)"
