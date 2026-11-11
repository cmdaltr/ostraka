"""Incidents end to end against Postgres: access, editing, observables, assets, findings, comments,
the activity chain, the timeline, references and live updates."""

import asyncio

import pytest
from sqlalchemy import select, text

from ostraka.models import Mention, Role, Team

pytestmark = pytest.mark.db


async def team_id(db, name: str) -> str:
    return str(await db.scalar(select(Team.id).where(Team.name == name)))


@pytest.fixture
def people(make_person, sign_in):
    """A small organisation: an admin, a SOC lead and responder, a DFIR responder and a SOC observer."""

    async def build():
        cast = {
            "admin": await make_person("admin@example.org", Role.ADMIN, None, name="Ada Admin"),
            "lead": await make_person("lead@example.org", Role.TEAM_LEAD, "SOC", name="Lee Lead"),
            "soc": await make_person("soc@example.org", Role.RESPONDER, "SOC", name="Sam Soc"),
            "dfir": await make_person("dfir@example.org", Role.RESPONDER, "Forensics", name="Dee Dfir"),
            "exec": await make_person("exec@example.org", Role.OBSERVER, "SOC", name="Eve Exec"),
        }
        for person in cast.values():
            await sign_in(person)
        return cast

    return build


async def declare(client, person, **overrides) -> dict:
    body = {"title": "Mailbox rule forwarding finance mail", "severity": "high", "tlp": "amber", **overrides}
    r = await client.post("/api/incidents", json=body, headers=person.headers)
    assert r.status_code == 201, r.text
    return r.json()


# ---- declaring and access -------------------------------------------------------------------------


async def test_declaring_makes_you_commander_and_brings_your_team(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    assert incident["ref"].startswith("OST-") and incident["ref"].endswith("-001")
    assert incident["commander"]["name"] == "Sam Soc"
    assert [t["name"] for t in incident["teams"]] == ["SOC"]
    assert incident["phase"] == "detect" and incident["status"] == "open"
    second = await declare(client, p["soc"], title="Second incident")
    assert second["ref"].endswith("-002")


async def test_observers_cannot_declare_or_edit(db, client, people):
    p = await people()
    assert (
        await client.post(
            "/api/incidents", json={"title": "Nope", "severity": "low"}, headers=p["exec"].headers
        )
    ).status_code == 403
    incident = await declare(client, p["soc"])
    seen = await client.get(f"/api/incidents/{incident['id']}", headers=p["exec"].headers)
    assert seen.status_code == 200 and seen.json()["can_edit"] is False
    r = await client.patch(
        f"/api/incidents/{incident['id']}", json={"version": 1, "title": "Changed"}, headers=p["exec"].headers
    )
    assert r.status_code == 403


async def test_incidents_are_visible_to_involved_teams_and_named_people_only(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    url = f"/api/incidents/{incident['id']}"
    assert (await client.get(url, headers=p["dfir"].headers)).status_code == 404
    assert (await client.get("/api/incidents", headers=p["dfir"].headers)).json() == []
    assert (await client.get(url, headers=p["admin"].headers)).status_code == 200

    added = await client.put(
        f"{url}/members/{p['dfir'].id}", json={"role": "responder"}, headers=p["soc"].headers
    )
    assert added.status_code == 200
    assert (await client.get(url, headers=p["dfir"].headers)).status_code == 200


async def test_bringing_in_a_team_opens_the_incident_to_it(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    url = f"/api/incidents/{incident['id']}"
    r = await client.post(
        f"{url}/teams", json={"team_id": await team_id(db, "Forensics")}, headers=p["lead"].headers
    )
    assert r.status_code == 200
    assert (await client.get(url, headers=p["dfir"].headers)).status_code == 200


async def test_stale_edits_are_refused_not_overwritten(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    url = f"/api/incidents/{incident['id']}"
    first = await client.patch(url, json={"version": 1, "phase": "scope"}, headers=p["soc"].headers)
    assert first.status_code == 200 and first.json()["version"] == 2
    stale = await client.patch(url, json={"version": 1, "title": "Overwritten?"}, headers=p["lead"].headers)
    assert stale.status_code == 409
    assert (await client.get(url, headers=p["soc"].headers)).json()["phase"] == "scope"


async def test_only_managers_change_tlp_or_close(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    url = f"/api/incidents/{incident['id']}"
    await client.put(f"{url}/members/{p['dfir'].id}", json={}, headers=p["soc"].headers)
    r = await client.patch(url, json={"version": 1, "tlp": "red"}, headers=p["dfir"].headers)
    assert r.status_code == 403
    r = await client.patch(
        url, json={"version": 1, "tlp": "red", "status": "closed"}, headers=p["soc"].headers
    )
    assert r.status_code == 200 and r.json()["tlp"] == "red" and r.json()["closed_at"]


async def test_handing_over_command(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    url = f"/api/incidents/{incident['id']}"
    r = await client.put(
        f"{url}/members/{p['lead'].id}", json={"role": "commander"}, headers=p["soc"].headers
    )
    roles = {m["user"]["name"]: m["role"] for m in r.json()["members"]}
    assert roles == {"Lee Lead": "commander", "Sam Soc": "responder"}
    assert (
        await client.delete(f"{url}/members/{p['lead'].id}", headers=p["lead"].headers)
    ).status_code == 409
    observer_role = await client.put(
        f"{url}/members/{p['exec'].id}", json={"role": "comms"}, headers=p["lead"].headers
    )
    assert observer_role.status_code == 422


# ---- observables ----------------------------------------------------------------------------------


async def test_observables_are_detected_shared_and_sighted(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    url = f"/api/incidents/{incident['id']}/observables"
    r = await client.post(
        url,
        json={
            "value": "hxxps://evil[.]example/gate.php",
            "sighting": {"host": "FIN-WS-022", "source": "Defender XDR", "seen_at": "2026-11-09T08:14:00Z"},
        },
        headers=p["soc"].headers,
    )
    assert r.status_code == 201
    item = r.json()
    assert item["kind"] == "url" and item["value"] == "https://evil.example/gate.php"
    assert item["ref"].startswith("OBS-") and len(item["sightings"]) == 1

    again = await client.post(
        url,
        json={"value": "HTTPS://EVIL.EXAMPLE/gate.php".lower(), "sighting": {"host": "FIN-WS-031"}},
        headers=p["soc"].headers,
    )
    assert again.json()["id"] == item["id"] and len(again.json()["sightings"]) == 2
    assert len((await client.get(url, headers=p["soc"].headers)).json()) == 1


async def test_overlap_names_visible_incidents_and_only_counts_hidden_ones(db, client, people):
    p = await people()
    soc_incident = await declare(client, p["soc"], title="SOC case")
    dfir_incident = await declare(client, p["dfir"], title="DFIR case")
    for who, incident in ((p["soc"], soc_incident), (p["dfir"], dfir_incident)):
        await client.post(
            f"/api/incidents/{incident['id']}/observables",
            json={"value": "185.220.101.47"},
            headers=who.headers,
        )

    as_soc = (
        await client.get(f"/api/incidents/{soc_incident['id']}/observables", headers=p["soc"].headers)
    ).json()
    assert as_soc[0]["elsewhere"] == {"visible": [], "hidden": 1, "ask": ["Forensics"]}

    as_admin = (
        await client.get(f"/api/incidents/{soc_incident['id']}/observables", headers=p["admin"].headers)
    ).json()
    assert as_admin[0]["elsewhere"]["visible"][0]["title"] == "DFIR case"


async def test_overlap_names_the_lead_to_ask(db, client, people, make_person, sign_in):
    p = await people()
    dfir_lead = await make_person("dlead@example.org", Role.TEAM_LEAD, "Forensics", name="Dana Lead")
    await sign_in(dfir_lead)
    a, b = await declare(client, p["soc"]), await declare(client, dfir_lead)
    for who, incident in ((p["soc"], a), (dfir_lead, b)):
        await client.post(
            f"/api/incidents/{incident['id']}/observables",
            json={"value": "evil-cdn.net"},
            headers=who.headers,
        )
    seen = (await client.get(f"/api/incidents/{a['id']}/observables", headers=p["soc"].headers)).json()
    assert seen[0]["elsewhere"]["ask"] == ["Forensics (lead: Dana Lead)"]


async def test_extract_previews_without_saving(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    url = f"/api/incidents/{incident['id']}/observables"
    await client.post(url, json={"value": "10.20.1.22"}, headers=p["soc"].headers)
    r = await client.post(
        f"{url}/extract",
        headers=p["soc"].headers,
        json={"text": "Beacon from 10.20.1.22 to hxxp://c2[.]example, dropped upd.exe (T1105)"},
    )
    found = {(e["kind"], e["value"], e["already_in_incident"]) for e in r.json()}
    assert ("ip_address", "10.20.1.22", True) in found
    assert ("url", "http://c2.example", False) in found
    assert len((await client.get(url, headers=p["soc"].headers)).json()) == 1


async def test_observers_see_types_not_values_until_they_opt_in(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    await client.post(f"{base}/observables", json={"value": "185.220.101.47"}, headers=p["soc"].headers)
    await client.post(
        f"{base}/comments", json={"body": "Beacon to 185.220.101.47 every 60s"}, headers=p["soc"].headers
    )

    observed = (await client.get(f"{base}/observables", headers=p["exec"].headers)).json()[0]
    assert observed["value"] is None and observed["masked"] is True and observed["kind"] == "ip_address"
    comment = (await client.get(f"{base}/comments", headers=p["exec"].headers)).json()[0]
    assert comment["body"] == "Beacon to [ip address] every 60s"

    await client.patch("/api/users/me", json={"show_raw_iocs": True}, headers=p["exec"].headers)
    assert (await client.get(f"{base}/observables", headers=p["exec"].headers)).json()[0][
        "value"
    ] == "185.220.101.47"


# ---- assets ---------------------------------------------------------------------------------------


async def test_only_one_patient_zero(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    url = f"/api/incidents/{incident['id']}/assets"
    a = (
        await client.post(
            url, json={"hostname": "FIN-WS-022", "is_patient_zero": True}, headers=p["soc"].headers
        )
    ).json()
    b = (
        await client.post(
            url, json={"hostname": "FIN-WS-031", "is_patient_zero": True}, headers=p["soc"].headers
        )
    ).json()
    listed = {
        x["hostname"]: x["is_patient_zero"] for x in (await client.get(url, headers=p["soc"].headers)).json()
    }
    assert listed == {"FIN-WS-022": False, "FIN-WS-031": True}
    r = await client.patch(
        f"{url}/{a['id']}", json={"version": a["version"], "is_patient_zero": True}, headers=p["soc"].headers
    )
    assert r.status_code == 200
    listed = {
        x["hostname"]: x["is_patient_zero"] for x in (await client.get(url, headers=p["soc"].headers)).json()
    }
    assert listed == {"FIN-WS-022": True, "FIN-WS-031": False}
    assert b["ref"].startswith("AST-")


async def test_asset_links_and_the_timeline(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    a = (
        await client.post(f"{base}/assets", json={"hostname": "FIN-WS-022"}, headers=p["soc"].headers)
    ).json()
    b = (
        await client.post(f"{base}/assets", json={"hostname": "FIN-SQL-01"}, headers=p["soc"].headers)
    ).json()
    r = await client.post(
        f"{base}/asset-links",
        headers=p["soc"].headers,
        json={
            "source_id": a["id"],
            "target_id": b["id"],
            "kind": "lateral_movement",
            "seen_at": "2026-11-09T09:00:00Z",
            "technique": "t1021.002",
        },
    )
    assert r.status_code == 201 and r.json()["technique"] == "T1021.002"
    await client.post(
        f"{base}/observables",
        headers=p["soc"].headers,
        json={
            "value": "185.220.101.47",
            "sighting": {"asset_id": a["id"], "seen_at": "2026-11-09T08:14:00Z"},
        },
    )
    await client.post(
        f"{base}/findings",
        headers=p["soc"].headers,
        json={
            "kind": "evidence",
            "title": "Credential dumping on FIN-WS-022",
            "occurred_at": "2026-11-09T08:40:00Z",
            "asset_id": a["id"],
            "techniques": ["T1003.001"],
        },
    )

    timeline = (await client.get(f"{base}/timeline", headers=p["soc"].headers)).json()
    assert [i["kind"] for i in timeline] == ["sighting", "finding", "link"]
    assert timeline[0]["asset"] == "FIN-WS-022"
    assert timeline[2]["title"] == "Lateral movement: FIN-WS-022 → FIN-SQL-01"


# ---- findings -------------------------------------------------------------------------------------


async def test_confirming_needs_a_verification(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}/findings"
    finding = (
        await client.post(
            base,
            headers=p["soc"].headers,
            json={"kind": "evidence", "title": "Inbox rule forwards to external address"},
        )
    ).json()
    assert finding["status"] == "lead" and finding["ref"].startswith("FND-")

    blocked = await client.post(
        f"{base}/{finding['id']}/confirm", json={"version": 1}, headers=p["soc"].headers
    )
    assert blocked.status_code == 409

    incomplete = await client.post(
        f"{base}/{finding['id']}/verifications",
        headers=p["dfir"].headers,
        json={"method": "search", "scope": "jsmith mailbox"},
    )
    assert incomplete.status_code in (403, 404, 422)  # dfir can't see it, and the body lacks the query

    r = await client.post(
        f"{base}/{finding['id']}/verifications",
        headers=p["lead"].headers,
        json={
            "method": "search",
            "platform": "Defender XDR",
            "scope": "jsmith@example.org mailbox",
            "query": "CloudAppEvents | where ActionType == 'New-InboxRule'",
            "result_count": 1,
            "time_from": "2026-11-01T00:00:00Z",
            "time_to": "2026-11-10T00:00:00Z",
        },
    )
    assert r.status_code == 201 and r.json()["verifications"][0]["ran_by"]["name"] == "Lee Lead"

    confirmed = await client.post(
        f"{base}/{finding['id']}/confirm", json={"version": 1}, headers=p["soc"].headers
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed" and confirmed.json()["decided_by"]["name"] == "Sam Soc"


async def test_refuting_needs_a_reason(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}/findings"
    finding = (
        await client.post(
            base, headers=p["soc"].headers, json={"kind": "potential", "title": "Possible VPN abuse"}
        )
    ).json()
    assert (
        await client.post(f"{base}/{finding['id']}/refute", json={"version": 1}, headers=p["soc"].headers)
    ).status_code == 422
    r = await client.post(
        f"{base}/{finding['id']}/refute",
        headers=p["soc"].headers,
        json={"version": 1, "note": "Logins were the user's own travel"},
    )
    assert r.json()["status"] == "refuted"


async def test_intelligence_grading_and_handling(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}/findings"
    no_conditions = await client.post(
        base,
        headers=p["soc"].headers,
        json={
            "kind": "intelligence",
            "title": "Actor reuses this C2",
            "source_reliability": "B",
            "information_credibility": "2",
            "handling_code": "C",
        },
    )
    assert no_conditions.status_code == 422
    r = await client.post(
        base,
        headers=p["soc"].headers,
        json={
            "kind": "intelligence",
            "title": "Actor reuses this C2",
            "source_reliability": "B",
            "information_credibility": "2",
            "handling_code": "C",
            "handling_conditions": "Not for onward sharing",
        },
    )
    assert r.status_code == 201 and (r.json()["source_reliability"], r.json()["information_credibility"]) == (
        "B",
        "2",
    )


# ---- comments -------------------------------------------------------------------------------------


async def test_threads_mentions_and_withdrawal(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}/comments"
    dfir = await team_id(db, "Forensics")
    first = (
        await client.post(
            base,
            headers=p["soc"].headers,
            json={"body": f"@[Lee Lead](user:{p['lead'].id}) can @[DFIR](team:{dfir}) take the image?"},
        )
    ).json()
    mentions = (await db.scalars(select(Mention))).all()
    assert {(m.user_id is not None, m.team_id is not None) for m in mentions} == {
        (True, False),
        (False, True),
    }

    reply = await client.post(
        base, headers=p["lead"].headers, json={"body": "On it", "parent_id": first["id"]}
    )
    assert reply.status_code == 201
    nested = await client.post(
        base, headers=p["soc"].headers, json={"body": "x", "parent_id": reply.json()["id"]}
    )
    assert nested.status_code == 422

    assert (
        await client.patch(
            f"{base}/{first['id']}", json={"version": 1, "body": "edit"}, headers=p["lead"].headers
        )
    ).status_code == 403
    assert (await client.delete(f"{base}/{first['id']}", headers=p["soc"].headers)).status_code == 204
    listed = (await client.get(base, headers=p["soc"].headers)).json()
    assert listed[0]["deleted"] is True and listed[0]["body"] == ""
    resolved = await client.post(f"{base}/{first['id']}/resolve", headers=p["soc"].headers)
    assert resolved.json()["resolved_at"]


# ---- the activity chain, references and tags -------------------------------------------------------


async def test_every_change_is_logged_and_the_chain_verifies(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    await client.patch(base, json={"version": 1, "phase": "contain"}, headers=p["soc"].headers)
    await client.post(f"{base}/observables", json={"value": "185.220.101.47"}, headers=p["soc"].headers)
    events = (await client.get(f"{base}/activity", headers=p["soc"].headers)).json()
    assert [e["action"] for e in events] == ["declared", "incident_updated", "observable_added"]
    assert [e["position"] for e in events] == [1, 2, 3]
    assert "phase detect → contain" in events[1]["summary"]
    assert (await client.get(f"{base}/activity/verify", headers=p["soc"].headers)).json() == {
        "ok": True,
        "events": 3,
        "broken_at": None,
    }

    await db.execute(text("UPDATE activity_events SET summary = 'nothing happened' WHERE position = 2"))
    await db.commit()
    assert (await client.get(f"{base}/activity/verify", headers=p["soc"].headers)).json()["broken_at"] == 2


async def test_references_resolve_only_for_people_who_can_see_them(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    r = await client.get(f"/api/refs/{incident['ref']}", headers=p["soc"].headers)
    assert r.status_code == 200 and r.json()["incident_id"] == incident["id"]
    assert (await client.get(f"/api/refs/{incident['ref']}", headers=p["dfir"].headers)).status_code == 404
    finding = (
        await client.post(
            f"/api/incidents/{incident['id']}/findings",
            headers=p["soc"].headers,
            json={"kind": "evidence", "title": "Something real"},
        )
    ).json()
    r = await client.get(f"/api/refs/{finding['ref']}", headers=p["soc"].headers)
    assert r.json()["entity_id"] == finding["id"]


async def test_tags_list_controlled_and_free(db, client, people):
    p = await people()
    await declare(client, p["soc"], tags=["Initial Access", "bec"])
    assert (
        await client.put(
            "/api/tags/bec",
            json={"name": "bec", "description": "Business email compromise"},
            headers=p["soc"].headers,
        )
    ).status_code == 403
    await client.put(
        "/api/tags/bec",
        json={"name": "bec", "description": "Business email compromise"},
        headers=p["admin"].headers,
    )
    tags = {
        t["name"]: t["controlled"] for t in (await client.get("/api/tags", headers=p["soc"].headers)).json()
    }
    assert tags == {"bec": True, "initial-access": False}


# ---- live updates -----------------------------------------------------------------------------------


async def test_live_socket_receives_changes_and_presence(db, client, people):
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from ostraka.main import create_app

    p = await people()
    incident = await declare(client, p["soc"])
    soc_headers, dfir_headers = p["soc"].headers, p["dfir"].headers

    def run() -> tuple[list, dict, int]:
        with TestClient(create_app()) as tc:
            ticket = tc.post("/api/live/ticket", headers=soc_headers).json()["ticket"]
            with tc.websocket_connect(f"/ws?ticket={ticket}") as ws:
                ws.send_json({"type": "join", "incident": incident["id"]})
                presence = ws.receive_json()
                tc.patch(
                    f"/api/incidents/{incident['id']}",
                    json={"version": 1, "phase": "scope"},
                    headers=soc_headers,
                )
                activity_message = ws.receive_json()
            # A ticket works once
            with pytest.raises(WebSocketDisconnect), tc.websocket_connect(f"/ws?ticket={ticket}") as again:
                again.receive_json()
            # Someone who can't see the incident can't join it
            other = tc.post("/api/live/ticket", headers=dfir_headers).json()["ticket"]
            with tc.websocket_connect(f"/ws?ticket={other}") as ws2:
                ws2.send_json({"type": "join", "incident": incident["id"]})
                refused = ws2.receive_json()
        return presence["people"], activity_message, refused

    people_present, message, refused = await asyncio.to_thread(run)
    assert [x["name"] for x in people_present] == ["Sam Soc"]
    assert message["type"] == "activity" and message["event"]["action"] == "incident_updated"
    assert "summary" not in message["event"] and "data" not in message["event"]  # masked only via the API
    assert refused["type"] == "error"
