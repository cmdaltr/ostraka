"""The chain of custody, its permission rules and the incident graph, without a database."""

import uuid
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ostraka import custody, graph
from ostraka.auth import policy
from ostraka.models import (
    AssetStatus,
    CustodyEntry,
    CustodyKind,
    EvidenceItem,
    FindingKind,
    FindingStatus,
    HashCheck,
    LinkKind,
    RecordKind,
    Role,
    Severity,
    Tlp,
    User,
    Verdict,
)
from ostraka.observables.detect import ObservableType

SHA = "a" * 64
T0 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def person(role: Role = Role.RESPONDER, *, active: bool = True, name: str = "X") -> User:
    return User(
        id=uuid.uuid4(), role=role, team_id=uuid.uuid4(), email="x@example.org", name=name, is_active=active
    )


# ---- policy -----------------------------------------------------------------------------------------


def test_observers_never_add_evidence_and_others_need_to_work_the_incident():
    assert policy.can_add_evidence(person(Role.OBSERVER), can_edit=False)
    assert policy.can_add_evidence(person(), can_edit=False)
    assert policy.can_add_evidence(person(), can_edit=True) is None


def test_the_holder_offers_custody_or_a_manager_on_their_behalf():
    holder, other = person(), person()
    assert policy.can_offer_custody(holder, holder_id=holder.id, can_manage=False, pending=False) is None
    assert "holding" in policy.can_offer_custody(other, holder_id=holder.id, can_manage=False, pending=False)
    assert policy.can_offer_custody(other, holder_id=holder.id, can_manage=True, pending=False) is None
    assert "withdraw it first" in policy.can_offer_custody(
        holder, holder_id=holder.id, can_manage=True, pending=True
    )
    assert policy.can_offer_custody(person(Role.OBSERVER), holder_id=None, can_manage=True, pending=False)


@pytest.mark.parametrize(
    ("recipient", "can_view", "is_holder", "allowed"),
    [
        (person(), True, False, True),
        (person(Role.ADMIN), True, False, True),
        (person(Role.OBSERVER), True, False, False),
        (person(active=False), True, False, False),
        (person(), False, False, False),
        (person(), True, True, False),
    ],
)
def test_who_may_be_offered_an_exhibit(recipient, can_view, is_holder, allowed):
    holder = recipient.id if is_holder else uuid.uuid4()
    refusal = policy.can_receive_custody(recipient, can_view=can_view, holder_id=holder)
    assert (refusal is None) is allowed


def test_only_the_recipient_answers_a_handover_and_the_offerer_holder_or_a_manager_withdraws_it():
    recipient, offerer, other = person(), person(), person()
    assert policy.can_answer_custody(recipient, recipient_id=recipient.id) is None
    assert policy.can_answer_custody(offerer, recipient_id=recipient.id)
    assert policy.can_answer_custody(person(Role.ADMIN), recipient_id=recipient.id)  # nobody on their behalf
    args = {"offered_by": offerer.id, "holder_id": uuid.uuid4()}
    assert policy.can_withdraw_custody(offerer, **args, can_manage=False) is None
    assert policy.can_withdraw_custody(other, **args, can_manage=False)
    assert policy.can_withdraw_custody(other, **args, can_manage=True) is None


# ---- the chain --------------------------------------------------------------------------------------


def exhibit() -> EvidenceItem:
    return EvidenceItem(
        id=uuid.uuid4(),
        seq=1,
        incident_id=uuid.uuid4(),
        label="Disk image",
        sha256=SHA,
        sha1="",
        md5="",
        size_bytes=10,
        location="Safe 1",
        created_at=T0,
    )


def add(item: EvidenceItem, entries: list, kind: CustodyKind, **fields) -> CustodyEntry:
    blank = {
        "at": T0 + timedelta(minutes=len(entries)),
        "actor_id": None,
        "actor_name": "Someone",
        "from_id": None,
        "from_name": "",
        "to_id": None,
        "to_name": "",
        "purpose": "",
        "location": "",
        "acknowledgement": "",
        "hash_check": None,
        "offer_position": None,
    }
    entry = CustodyEntry(
        evidence_id=item.id,
        position=len(entries) + 1,
        kind=kind,
        recorded_at=T0 + timedelta(minutes=len(entries)),
        prev_hash=entries[-1].hash if entries else custody.anchor(item),
        **(blank | fields),
    )
    entry.hash = custody.digest(entry)
    entries.append(entry)
    return entry


def handed_over() -> tuple[EvidenceItem, list, uuid.UUID, uuid.UUID]:
    item, entries = exhibit(), []
    jun, tom = uuid.uuid4(), uuid.uuid4()
    add(item, entries, CustodyKind.RECEIVED, to_id=jun, to_name="Jun", location="Safe 1")
    add(
        item,
        entries,
        CustodyKind.OFFERED,
        from_id=jun,
        from_name="Jun",
        to_id=tom,
        to_name="Tom",
        location="Lab",
    )
    add(
        item,
        entries,
        CustodyKind.ACCEPTED,
        from_id=jun,
        from_name="Jun",
        to_id=tom,
        to_name="Tom",
        location="Lab",
        acknowledgement="Seal intact",
        hash_check=HashCheck.MATCH,
        offer_position=2,
    )
    return item, entries, jun, tom


def test_custody_moves_only_on_acceptance():
    item, entries, jun, tom = handed_over()
    offered = custody.state(item, entries[:2])
    assert offered.holder_id == jun and offered.location == "Safe 1" and offered.pending is entries[1]
    now = custody.state(item, entries)
    assert now.holder_id == tom and now.holder_name == "Tom" and now.location == "Lab"
    assert now.pending is None and not now.mismatch


def test_a_declined_or_withdrawn_offer_leaves_custody_where_it_was():
    item, entries = exhibit(), []
    jun = uuid.uuid4()
    add(item, entries, CustodyKind.RECEIVED, to_id=jun, to_name="Jun")
    add(item, entries, CustodyKind.OFFERED, to_id=uuid.uuid4(), to_name="Tom")
    add(item, entries, CustodyKind.DECLINED, offer_position=2)
    assert custody.state(item, entries).pending is None
    assert custody.state(item, entries).holder_id == jun


def test_a_hash_mismatch_on_receipt_is_kept_in_the_state():
    item, entries = exhibit(), []
    add(item, entries, CustodyKind.RECEIVED, to_id=uuid.uuid4(), to_name="Jun")
    add(item, entries, CustodyKind.OFFERED, to_id=uuid.uuid4(), to_name="Tom")
    add(item, entries, CustodyKind.ACCEPTED, hash_check=HashCheck.MISMATCH, offer_position=2)
    assert custody.state(item, entries).mismatch


def test_the_chain_verifies_and_any_change_breaks_it():
    item, entries, _, _ = handed_over()
    assert custody.verify(item, entries).ok
    entries[1].purpose = "Something else"
    assert custody.verify(item, entries) == custody.ChainCheck(False, 3, 2)


def test_the_chain_is_anchored_to_the_exhibits_hashes():
    item, entries, _, _ = handed_over()
    item.sha256 = "b" * 64
    assert custody.verify(item, entries).broken_at == 1


def test_a_removed_entry_breaks_the_chain():
    item, entries, _, _ = handed_over()
    assert custody.verify(item, [entries[0], entries[2]]).broken_at == 3


def test_times_hash_the_same_in_any_zone():
    item, entries = exhibit(), []
    entry = add(item, entries, CustodyKind.RECEIVED, at=T0)
    entry.at = T0.astimezone(timezone(timedelta(hours=1)))
    assert custody.digest(entry) == entry.hash


def test_hashes_are_cleaned_and_checked():
    assert custody.clean_hash(" AB:" + "c" * 62, custody.SHA256, "SHA-256") == "ab" + "c" * 62
    assert custody.clean_hash("", custody.MD5, "MD5") == ""
    with pytest.raises(ValueError, match="required"):
        custody.clean_hash("", custody.SHA256, "SHA-256", required=True)
    with pytest.raises(ValueError, match="64 hexadecimal"):
        custody.clean_hash("xyz", custody.SHA256, "SHA-256")


# ---- the graph --------------------------------------------------------------------------------------


def _scene():
    incident = SimpleNamespace(
        id=uuid.uuid4(), ref="OST-2026-001", tlp=Tlp.AMBER_STRICT, kind=RecordKind.INCIDENT
    )
    ws = SimpleNamespace(
        id=uuid.uuid4(), seq=1, hostname="FIN-WS-0142", is_patient_zero=True,
        status=AssetStatus.CONTAINED, criticality=Severity.MEDIUM,
    )  # fmt: skip
    fs = SimpleNamespace(
        id=uuid.uuid4(), seq=2, hostname="FS01", is_patient_zero=False,
        status=AssetStatus.IDENTIFIED, criticality=Severity.HIGH,
    )  # fmt: skip
    c2 = SimpleNamespace(
        id=uuid.uuid4(),
        observable=SimpleNamespace(seq=7, kind=ObservableType.IP_ADDRESS, value="203.0.113.45"),
        verdict=Verdict.MALICIOUS,
        sightings=[
            SimpleNamespace(asset_id=ws.id, host="", seen_at=T0, source="Defender XDR"),
            SimpleNamespace(
                asset_id=None, host="fin-ws-0142", seen_at=T0 + timedelta(hours=1), source="FW01"
            ),
            SimpleNamespace(asset_id=None, host="unknown-host", seen_at=T0, source="FW01"),
        ],
    )
    item, entries, _, _ = handed_over()
    item.entries = entries
    finding = SimpleNamespace(
        id=uuid.uuid4(), seq=3, title="Beacon to 203.0.113.45 from FIN-WS-0142",
        status=FindingStatus.CONFIRMED,
        kind=FindingKind.EVIDENCE, occurred_at=T0, asset_id=ws.id, techniques=["T1071.001"],
        observable_links=[SimpleNamespace(incident_observable_id=c2.id)],
    )  # fmt: skip
    lead = SimpleNamespace(
        id=uuid.uuid4(), seq=4, title="Admin share use", status=FindingStatus.LEAD,
        kind=FindingKind.POTENTIAL,
        occurred_at=None, asset_id=None, techniques=["T1021.002"], observable_links=[],
    )  # fmt: skip
    move = SimpleNamespace(
        id=uuid.uuid4(), source_id=ws.id, target_id=fs.id, kind=LinkKind.LATERAL_MOVEMENT,
        seen_at=T0 - timedelta(hours=2), technique="T1021.002", source="Defender XDR", note="via 10.0.0.9",
    )  # fmt: skip
    return incident, [ws, fs], c2, item, [finding, lead], move


def _build(masked: bool):
    incident, assets, c2, item, findings, move = _scene()
    return graph.build(
        incident,
        masked=masked,
        mask=(lambda t: t.replace("203.0.113.45", "[ip address]").replace("10.0.0.9", "[ip address]"))
        if masked
        else (lambda t: t),
        observables=[c2],
        assets=assets,
        connections=[move],
        findings=findings,
        citations=[(findings[0].id, item.id)],
        exhibits=[item],
    ), (incident, assets, c2, item, findings, move)


def test_the_graph_joins_what_is_already_recorded():
    out, (incident, (ws, fs), c2, item, (finding, lead), move) = _build(False)
    types = sorted(n.type for n in out.nodes)
    assert types == [
        "asset",
        "asset",
        "evidence",
        "finding",
        "finding",
        "observable",
        "technique",
        "technique",
    ]
    kinds = sorted(e.kind for e in out.edges)
    assert kinds == sorted(
        ["sighted_on", "cites_observable", "about_asset", "uses_technique", "uses_technique",
         "cites_evidence", "connection", "connection_technique"]
    )  # fmt: skip
    sighting = next(e for e in out.edges if e.kind == "sighted_on")
    # Two sightings on the workstation, one by asset and one by hostname; the unknown host has no node
    assert sighting.attrs["sightings"] == 2 and sighting.attrs["sources"] == ["Defender XDR", "FW01"]
    assert sighting.source == f"observable:{c2.id}" and sighting.target == f"asset:{ws.id}"
    connection = next(e for e in out.edges if e.kind == "connection")
    assert (
        connection.attrs["link_kind"] == "lateral_movement" and connection.attrs["source"] == "Defender XDR"
    )
    evidence = next(n for n in out.nodes if n.type == "evidence")
    assert evidence.ref == "EXH-1" and evidence.attrs["holder"] == "Tom"
    assert out.restricted and out.tlp is Tlp.AMBER_STRICT


def test_the_techniques_summary_counts_findings_by_status_and_connections():
    out, _ = _build(False)
    by_id = {t.technique_id: t for t in out.techniques}
    assert list(by_id) == ["T1021.002", "T1071.001"]
    smb = by_id["T1021.002"]
    assert (smb.confirmed, smb.leads, smb.refuted, smb.connections) == (0, 1, 0, 1)
    assert smb.findings == ["FND-4"] and smb.first_seen == T0 - timedelta(hours=2)
    assert smb.name == "SMB/Windows Admin Shares" and smb.tactics == ["lateral-movement"]
    assert by_id["T1071.001"].confirmed == 1


def test_a_masked_viewer_gets_no_indicator_values():
    out, _ = _build(True)
    observable = next(n for n in out.nodes if n.type == "observable")
    assert observable.label == "[ip address]" and observable.attrs["masked"] and out.masked
    text = out.model_dump_json()
    assert "203.0.113.45" not in text and "10.0.0.9" not in text
