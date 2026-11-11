"""Exhibits, the chain of custody and the incident graph against Postgres."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from ostraka.models import ActivityEvent, Notification, SecurityEvent
from tests.test_incidents import declare, people  # noqa: F401  (the fixture)

pytestmark = pytest.mark.db

DISK = "4b1f0e7c9a2d83e65f1c0b9a7e4d2c8f6a3b5e1d9c7f0a2b4e6d8c1a3f5b7e9d"
EXHIBIT = {
    "label": "FIN-WS-07 disk image (E01)",
    "description": "Imaged on the write-blocker.",
    "sha256": DISK.upper(),
    "md5": "7c2e9f4a1b8d3e6c5f0a9b2d4e7c1f83",
    "size_bytes": 1024,
    "location": "Safe 2, bag SB-40117",
}


async def _setup(client, p):
    incident = await declare(client, p["soc"], tlp="amber+strict")
    base = f"/api/incidents/{incident['id']}"
    # Forensics joins, so Dee can receive evidence
    teams = (await client.get("/api/teams", headers=p["admin"].headers)).json()
    forensics = next(t for t in teams if t["name"] == "Forensics")
    r = await client.post(f"{base}/teams", json={"team_id": forensics["id"]}, headers=p["soc"].headers)
    assert r.status_code in (200, 201), r.text
    return incident, base


async def test_an_exhibit_needs_a_sha256_and_observers_cannot_add_one(db, client, people):  # noqa: F811
    p = await people()
    _, base = await _setup(client, p)
    r = await client.post(f"{base}/evidence", json={**EXHIBIT, "sha256": ""}, headers=p["soc"].headers)
    assert r.status_code == 422
    r = await client.post(f"{base}/evidence", json={**EXHIBIT, "sha256": "abc"}, headers=p["soc"].headers)
    assert r.status_code == 422
    r = await client.post(f"{base}/evidence", json=EXHIBIT, headers=p["exec"].headers)
    assert r.status_code == 403

    r = await client.post(f"{base}/evidence", json=EXHIBIT, headers=p["soc"].headers)
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["ref"].startswith("EXH-") and made["sha256"] == DISK  # lower-cased
    assert made["holder"]["name"] == "Sam Soc" and made["chain"] == {
        "ok": True,
        "entries": 1,
        "broken_at": None,
        "head": made["entries"][0]["hash"],
    }
    assert made["entries"][0]["kind"] == "received" and made["can_offer"]

    # The observer reads it with the hashes withheld
    seen = (await client.get(f"{base}/evidence", headers=p["exec"].headers)).json()[0]
    assert seen["masked"] and seen["sha256"] is None and not seen["can_offer"]


async def test_custody_moves_when_the_recipient_accepts_and_is_on_the_activity_chain(
    db,
    outbox,  # deliveries are captured, not sent
    client,
    people,  # noqa: F811
):
    p = await people()
    _, base = await _setup(client, p)
    made = (await client.post(f"{base}/evidence", json=EXHIBIT, headers=p["soc"].headers)).json()
    url = f"{base}/evidence/{made['id']}"

    # The holder offers, or someone who manages the incident on their behalf; a responder who isn't
    # holding it can't. Observers can't receive.
    to_dee = {"to_id": str(p["dfir"].id), "purpose": "Analysis"}
    r = await client.post(f"{url}/offer", json=to_dee, headers=p["dfir"].headers)
    assert r.status_code == 403
    r = await client.post(f"{url}/offer", json=to_dee, headers=p["lead"].headers)
    assert r.status_code == 200 and r.json()["pending"]["actor"]["name"] == "Lee Lead"
    assert r.json()["pending"]["from_person"]["name"] == "Sam Soc"
    r = await client.post(f"{url}/withdraw", json={"note": "Sam will do it"}, headers=p["lead"].headers)
    assert r.status_code == 200 and r.json()["pending"] is None
    r = await client.post(
        f"{url}/offer", json={"to_id": str(p["exec"].id), "purpose": "For a look"}, headers=p["soc"].headers
    )
    assert r.status_code == 422 and "Observers" in r.json()["detail"]
    r = await client.post(
        f"{url}/offer",
        json={"to_id": str(p["dfir"].id), "purpose": "Timeline analysis", "location": "DFIR lab"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text
    offered = r.json()
    assert offered["holder"]["name"] == "Sam Soc" and offered["pending"]["to_person"]["name"] == "Dee Dfir"
    r = await client.post(
        f"{url}/offer", json={"to_id": str(p["lead"].id), "purpose": "Again"}, headers=p["soc"].headers
    )
    assert r.status_code == 409

    # Dee was told; nobody but Dee may accept
    note = await db.scalar(select(Notification).where(Notification.user_id == p["dfir"].id))
    assert note is not None and "custody" in note.title
    for who in ("soc", "admin"):
        r = await client.post(f"{url}/accept", json={"acknowledgement": "Got it"}, headers=p[who].headers)
        assert r.status_code == 403
    r = await client.post(
        f"{url}/accept",
        json={"acknowledgement": "Seal SB-40117 intact", "sha256": DISK},
        headers=p["dfir"].headers,
    )
    assert r.status_code == 200, r.text
    held = r.json()
    assert held["holder"]["name"] == "Dee Dfir" and held["location"] == "DFIR lab" and held["pending"] is None
    assert held["entries"][-1]["hash_check"] == "match" and held["chain"]["ok"]
    positions = [(e["position"], e["kind"]) for e in held["entries"]]
    assert positions[-2:] == [(positions[-2][0], "offered"), (positions[-1][0], "accepted")]

    # Every step is on the incident's hash chain, carrying the custody entry's hash, and in the security log
    events = (await db.scalars(select(ActivityEvent).where(ActivityEvent.entity_type == "exhibit"))).all()
    assert {e.data["custody_hash"] for e in events} == {e["hash"] for e in held["entries"]}
    chain = (await client.get(f"{base}/activity/verify", headers=p["soc"].headers)).json()
    assert chain["ok"]
    logged = (await db.scalars(select(SecurityEvent.action))).all()
    assert {"evidence_added", "custody_offered", "custody_accepted"} <= set(logged)


async def test_a_mismatched_hash_is_recorded_not_refused_and_declines_leave_custody(
    db,
    outbox,
    client,
    people,  # noqa: F811
):
    p = await people()
    _, base = await _setup(client, p)
    made = (await client.post(f"{base}/evidence", json=EXHIBIT, headers=p["soc"].headers)).json()
    url = f"{base}/evidence/{made['id']}"
    offer = {"to_id": str(p["dfir"].id), "purpose": "Analysis"}
    await client.post(f"{url}/offer", json=offer, headers=p["soc"].headers)
    r = await client.post(f"{url}/decline", json={"note": "Not mine"}, headers=p["dfir"].headers)
    assert r.json()["holder"]["name"] == "Sam Soc" and r.json()["pending"] is None
    await client.post(f"{url}/offer", json=offer, headers=p["soc"].headers)
    r = await client.post(
        f"{url}/accept", json={"acknowledgement": "Re-hashed", "sha256": "f" * 64}, headers=p["dfir"].headers
    )
    assert r.status_code == 200 and r.json()["hash_mismatch"]
    assert r.json()["entries"][-1]["hash_check"] == "mismatch"


async def test_custody_cannot_be_rewritten_or_removed_in_the_database(db, client, people):  # noqa: F811
    p = await people()
    _, base = await _setup(client, p)
    made = (await client.post(f"{base}/evidence", json=EXHIBIT, headers=p["soc"].headers)).json()
    for statement in (
        "UPDATE custody_entries SET purpose = 'rewritten'",
        "DELETE FROM custody_entries",
        "UPDATE evidence_items SET sha256 = repeat('b', 64)",
        "DELETE FROM evidence_items",
    ):
        with pytest.raises(DBAPIError):
            await db.execute(text(statement))
        await db.rollback()
    # The words can change, with a version; the hashes can't, and the chain still verifies
    url = f"{base}/evidence/{made['id']}"
    r = await client.patch(url, json={"version": 1, "description": "Imaged twice."}, headers=p["soc"].headers)
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["chain"]["ok"]
    r = await client.patch(url, json={"version": 1, "description": "Stale"}, headers=p["soc"].headers)
    assert r.status_code == 409
    # Removing the incident takes its exhibits with it
    await db.execute(text("DELETE FROM incidents"))
    await db.commit()
    assert await db.scalar(text("SELECT count(*) FROM custody_entries")) == 0


async def test_the_graph_links_findings_exhibits_assets_and_techniques_masked_per_viewer(
    db,
    client,
    people,  # noqa: F811
):
    p = await people()
    incident, base = await _setup(client, p)
    h = p["soc"].headers
    ws = (await client.post(f"{base}/assets", json={"hostname": "FIN-WS-07"}, headers=h)).json()
    fs = (await client.post(f"{base}/assets", json={"hostname": "FS01"}, headers=h)).json()
    r = await client.post(
        f"{base}/asset-links",
        json={
            "source_id": ws["id"],
            "target_id": fs["id"],
            "kind": "lateral_movement",
            "technique": "T1021.002",
            "source": "Defender XDR",
        },
        headers=h,
    )
    assert r.status_code == 201 and r.json()["source"] == "Defender XDR"
    ip = (
        await client.post(
            f"{base}/observables",
            json={"value": "203.0.113.45", "sighting": {"host": "fin-ws-07", "source": "FW01"}},
            headers=h,
        )
    ).json()
    finding = (
        await client.post(
            f"{base}/findings",
            json={
                "kind": "evidence",
                "title": "Beacon to 203.0.113.45",
                "asset_id": ws["id"],
                "techniques": ["T1071.001"],
                "observable_ids": [ip["id"]],
            },
            headers=h,
        )
    ).json()
    exhibit = (await client.post(f"{base}/evidence", json=EXHIBIT, headers=h)).json()
    cite = f"{base}/findings/{finding['id']}/evidence"
    assert (await client.post(cite, json={"evidence_id": exhibit["id"]}, headers=h)).status_code == 201
    assert (
        await client.post(cite, json={"evidence_id": exhibit["id"]}, headers=p["exec"].headers)
    ).status_code == 403

    out = (await client.get(f"{base}/graph", headers=h)).json()
    assert out["restricted"] and out["tlp"] == "amber+strict" and not out["masked"]
    assert sorted(e["kind"] for e in out["edges"]) == sorted(
        ["sighted_on", "cites_observable", "about_asset", "uses_technique", "cites_evidence",
         "connection", "connection_technique"]
    )  # fmt: skip
    assert [t["technique_id"] for t in out["techniques"]] == ["T1021.002", "T1071.001"]
    assert any(n["label"] == "203.0.113.45" for n in out["nodes"])

    masked = (await client.get(f"{base}/graph", headers=p["exec"].headers)).json()
    assert masked["masked"] and "203.0.113.45" not in str(masked)

    listed = (await client.get(f"{base}/evidence/{exhibit['id']}", headers=h)).json()
    assert listed["cited_by"] == [{"id": finding["id"], "ref": finding["ref"]}]
    r = await client.delete(f"{cite}/{exhibit['id']}", headers=h)
    assert r.status_code == 204
    assert (await client.get(f"{base}/evidence/{exhibit['id']}", headers=h)).json()["cited_by"] == []

    # Someone who can't see the incident gets nothing
    other = await declare(client, p["dfir"], title="Unrelated")
    assert (
        await client.get(f"/api/incidents/{other['id']}/graph", headers=p["soc"].headers)
    ).status_code == 404
