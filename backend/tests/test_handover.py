"""Handover and presence end to end against Postgres: "since you left", the generated handover note,
presence on objects, version checks on every edit, and warnings about the same indicator being worked
in another incident."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from ostraka import live
from ostraka.models import ActivityEvent, HandoverNote, Role
from tests.test_incidents import declare, team_id

pytestmark = pytest.mark.db

IP = "185.220.101.4"


@pytest.fixture
def people(make_person, sign_in):
    async def build():
        cast = {
            "admin": await make_person("admin@example.org", Role.ADMIN, None, name="Ada Admin"),
            "soc": await make_person("soc@example.org", Role.RESPONDER, "SOC", name="Sam Soc"),
            "soc2": await make_person("soc2@example.org", Role.RESPONDER, "SOC", name="Sky Soc"),
            "exec": await make_person("exec@example.org", Role.OBSERVER, "SOC", name="Eve Exec"),
            "dfir": await make_person("dfir@example.org", Role.RESPONDER, "Forensics", name="Dee Dfir"),
        }
        for person in cast.values():
            await sign_in(person)
        return cast

    return build


async def add_observable(client, person, incident, value=IP) -> dict:
    r = await client.post(
        f"/api/incidents/{incident['id']}/observables", json={"value": value}, headers=person.headers
    )
    assert r.status_code == 201, r.text
    return r.json()


async def add_task(client, person, incident, **body) -> dict:
    r = await client.post(
        f"/api/incidents/{incident['id']}/tasks",
        json={"title": "Image the finance file server", **body},
        headers=person.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---- since you left -------------------------------------------------------------------------------


async def test_since_you_left_lists_what_others_changed_after_your_last_view(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"

    first = (await client.get(f"{base}/since", headers=p["soc2"].headers)).json()
    assert first["first_visit"] is True and first["events"] == []
    assert (await client.post(f"{base}/seen", json={}, headers=p["soc2"].headers)).status_code == 204

    await add_observable(client, p["soc"], incident)
    await add_task(client, p["soc"], incident, team_id=await team_id(db, "SOC"))
    await client.patch(base, json={"version": 1, "phase": "scope"}, headers=p["soc"].headers)
    await add_observable(client, p["soc2"], incident, "evil.example.com")  # your own: not news

    since = (await client.get(f"{base}/since", headers=p["soc2"].headers)).json()
    assert since["first_visit"] is False and since["total"] == 3
    assert since["people"] == ["Sam Soc"]
    assert {c["entity_type"]: c["count"] for c in since["changes"]} == {
        "observable": 1,
        "task": 1,
        "incident": 1,
    }
    assert [e["action"] for e in since["events"]] == ["observable_added", "task_added", "incident_updated"]
    # Reading it doesn't move your place
    assert (await client.get(f"{base}/since", headers=p["soc2"].headers)).json()["total"] == 3

    await client.post(f"{base}/seen", json={"position": since["through_position"]}, headers=p["soc2"].headers)
    assert (await client.get(f"{base}/since", headers=p["soc2"].headers)).json()["total"] == 0
    # It never moves backwards, and can't run past the log
    await client.post(f"{base}/seen", json={"position": 1}, headers=p["soc2"].headers)
    assert (await client.get(f"{base}/since", headers=p["soc2"].headers)).json()["total"] == 0
    await client.post(f"{base}/seen", json={"position": 9999}, headers=p["soc2"].headers)
    await client.patch(base, json={"version": 2, "phase": "contain"}, headers=p["soc"].headers)
    assert (await client.get(f"{base}/since", headers=p["soc2"].headers)).json()["total"] == 1


async def test_since_you_left_is_masked_and_closed_to_outsiders(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    await client.post(f"{base}/seen", json={}, headers=p["exec"].headers)
    await add_observable(client, p["soc"], incident)
    since = (await client.get(f"{base}/since", headers=p["exec"].headers)).json()
    assert since["total"] == 1 and IP not in since["events"][0]["summary"]
    assert since["events"][0]["data"] == {}
    assert (await client.get(f"{base}/since", headers=p["dfir"].headers)).status_code == 404
    assert (await client.post(f"{base}/seen", json={}, headers=p["dfir"].headers)).status_code == 404


# ---- the handover note ----------------------------------------------------------------------------


async def test_a_handover_note_is_drafted_edited_and_posted(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    await add_observable(client, p["soc"], incident)
    await add_task(
        client, p["soc"], incident, team_id=await team_id(db, "SOC"), due_at="2020-01-01T09:00:00Z"
    )
    await client.post(
        f"{base}/findings", json={"kind": "evidence", "title": "Beacon to the VPS"}, headers=p["soc"].headers
    )

    draft = (await client.get(f"{base}/handover/draft", headers=p["soc"].headers)).json()
    assert draft["from_position"] == 0 and draft["through_position"] == 4
    assert (
        draft["text"] == (await client.get(f"{base}/handover/draft", headers=p["soc"].headers)).json()["text"]
    )
    titles = [s["title"] for s in draft["sections"]]
    assert titles[:3] == ["State", "Deadlines in the next 24 hours", "Open tasks (1)"]
    assert "SOC queue, unclaimed, overdue since 01 Jan 09:00 UTC" in draft["text"]
    assert "New: FND-1 Beacon to the VPS (lead)" in draft["text"]
    assert "Commander: Sam Soc." in draft["text"]

    body = draft["text"] + f"\n\nWatch {IP}: Dee is pulling the firewall logs."
    posted = await client.post(
        f"{base}/handover",
        json={"body": body, "from_position": 0, "through_position": draft["through_position"]},
        headers=p["soc"].headers,
    )
    assert posted.status_code == 201, posted.text
    note = posted.json()
    assert note["ref"].startswith("HND-") and note["edited"] is True and note["author"]["name"] == "Sam Soc"
    stored = await db.scalar(select(HandoverNote))
    assert stored.generated.strip() == draft["text"].strip() and stored.body == body

    event = await db.scalar(select(ActivityEvent).where(ActivityEvent.action == "handover_posted"))
    assert event.entity_type == "handover" and event.data["covers"] == [0, 4]

    # The next draft starts where this one ended
    later = (await client.get(f"{base}/handover/draft", headers=p["soc2"].headers)).json()
    assert later["from_position"] == 4 and later["through_position"] == 5

    listed = (await client.get(f"{base}/handover", headers=p["exec"].headers)).json()
    assert len(listed) == 1 and IP not in listed[0]["body"]
    assert IP in (await client.get(f"{base}/handover", headers=p["soc2"].headers)).json()[0]["body"]


async def test_only_responders_post_handovers_and_only_over_the_log_that_exists(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    assert (await client.get(f"{base}/handover/draft", headers=p["exec"].headers)).status_code == 403
    note = {"body": "All quiet.", "from_position": 0, "through_position": 1}
    assert (await client.post(f"{base}/handover", json=note, headers=p["exec"].headers)).status_code == 403
    assert (await client.post(f"{base}/handover", json=note, headers=p["dfir"].headers)).status_code == 404
    too_far = {**note, "through_position": 50}
    assert (await client.post(f"{base}/handover", json=too_far, headers=p["soc"].headers)).status_code == 422
    backwards = {**note, "from_position": 1, "through_position": 0}
    assert (
        await client.post(f"{base}/handover", json=backwards, headers=p["soc"].headers)
    ).status_code == 422
    r = await client.post(f"{base}/handover", json=note, headers=p["soc"].headers)
    assert r.status_code == 201 and r.json()["edited"] is True


# ---- versions on every edit -----------------------------------------------------------------------


async def test_stale_edits_to_comments_threads_and_task_claims_are_refused(db, client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    comments = f"/api/incidents/{incident['id']}/comments"
    comment = (await client.post(comments, json={"body": "First look"}, headers=p["soc"].headers)).json()
    assert comment["version"] == 1
    edited = await client.patch(
        f"{comments}/{comment['id']}",
        json={"version": 1, "body": "First look, revised"},
        headers=p["soc"].headers,
    )
    assert edited.status_code == 200 and edited.json()["version"] == 2
    stale = await client.patch(
        f"{comments}/{comment['id']}", json={"version": 1, "body": "Lost update"}, headers=p["soc"].headers
    )
    assert stale.status_code == 409
    no_version = await client.patch(
        f"{comments}/{comment['id']}", json={"body": "x"}, headers=p["soc"].headers
    )
    assert no_version.status_code == 422

    # Two people resolving the same thread: the second is told, rather than reopening it
    resolved = await client.post(
        f"{comments}/{comment['id']}/resolve", json={"version": 2}, headers=p["soc"].headers
    )
    assert resolved.status_code == 200 and resolved.json()["resolved_at"]
    again = await client.post(
        f"{comments}/{comment['id']}/resolve", json={"version": 2}, headers=p["soc2"].headers
    )
    assert again.status_code == 409

    task = await add_task(client, p["soc"], incident, team_id=await team_id(db, "SOC"))
    tasks = f"/api/incidents/{incident['id']}/tasks/{task['id']}"
    claimed = await client.post(
        f"{tasks}/claim", json={"version": task["version"]}, headers=p["soc2"].headers
    )
    assert claimed.status_code == 200
    stale_release = await client.post(
        f"{tasks}/release", json={"version": task["version"]}, headers=p["soc2"].headers
    )
    assert stale_release.status_code == 409
    released = await client.post(
        f"{tasks}/release", json={"version": claimed.json()["version"]}, headers=p["soc2"].headers
    )
    assert released.status_code == 200


async def test_stale_changes_to_teams_and_accounts_are_refused(db, client, people):
    p = await people()
    soc = await team_id(db, "SOC")
    # The seed teams outlive each test, so start from whatever version SOC is at now
    listed = (await client.get("/api/teams", headers=p["admin"].headers)).json()
    version = next(t["version"] for t in listed if t["id"] == soc)
    r = await client.patch(
        f"/api/teams/{soc}", json={"version": version, "description": "Tier 1"}, headers=p["admin"].headers
    )
    assert r.status_code == 200 and r.json()["version"] == version + 1
    r = await client.patch(
        f"/api/teams/{soc}", json={"version": version, "description": "Lost"}, headers=p["admin"].headers
    )
    assert r.status_code == 409

    url = f"/api/users/{p['soc2'].id}"
    r = await client.patch(url, json={"version": 1, "role": "team_lead"}, headers=p["admin"].headers)
    assert r.status_code == 200 and r.json()["version"] == 2
    r = await client.patch(url, json={"version": 1, "is_active": False}, headers=p["admin"].headers)
    assert r.status_code == 409


# ---- presence and work elsewhere ------------------------------------------------------------------


async def test_presence_carries_ids_only(db, client, people):
    from starlette.testclient import TestClient

    from ostraka.main import create_app

    p = await people()
    incident = await declare(client, p["soc"])
    item = await add_observable(client, p["soc"], incident)
    headers = p["soc"].headers

    def run() -> tuple[dict, dict]:
        with TestClient(create_app()) as tc:
            ticket = tc.post("/api/live/ticket", headers=headers).json()["ticket"]
            with tc.websocket_connect(f"/ws?ticket={ticket}") as ws:
                ws.send_json({"type": "join", "incident": incident["id"]})
                ws.receive_json()
                ws.send_json(
                    {
                        "type": "presence",
                        "incident": incident["id"],
                        "focus": f"observable:{item['id']}",
                        "editing": True,
                    }
                )
                good = ws.receive_json()
                ws.send_json({"type": "presence", "incident": incident["id"], "focus": f"observable:{IP}"})
                bad = ws.receive_json()
        return good, bad

    good, bad = await asyncio.to_thread(run)
    assert good["people"] == [
        {"user_id": str(p["soc"].id), "name": "Sam Soc", "focus": f"observable:{item['id']}", "editing": True}
    ]
    assert bad["people"][0]["focus"] == ""


async def test_work_on_the_same_indicator_elsewhere_is_flagged_without_leaking(db, client, people):
    p = await people()
    mine = await declare(client, p["soc"], title="Phishing at finance")
    here = await add_observable(client, p["soc"], mine)

    url = f"/api/incidents/{mine['id']}/observables/working"
    assert (await client.get(url, headers=p["soc"].headers)).json() == []

    # Dee works the same address in a Forensics incident Sam can't see: counted, with the team to ask
    theirs = await declare(client, p["dfir"], title="Beaconing from the print server")
    their_item = await add_observable(client, p["dfir"], theirs)
    # ... and in a TLP:RED one, which is never mentioned to outsiders
    red = await declare(client, p["dfir"], title="Insider case", tlp="red")
    await add_observable(client, p["dfir"], red)

    working = (await client.get(url, headers=p["soc"].headers)).json()
    assert working == [{"item_id": here["id"], "visible": [], "hidden": 1, "ask": ["Forensics"]}]

    # Once Sam is on that incident too, it's named, with who is working it
    r = await client.put(
        f"/api/incidents/{theirs['id']}/members/{p['soc'].id}",
        json={"role": "responder"},
        headers=p["dfir"].headers,
    )
    assert r.status_code == 200
    working = (await client.get(url, headers=p["soc"].headers)).json()
    assert working[0]["hidden"] == 0
    [seen] = working[0]["visible"]
    assert seen["ref"] == theirs["ref"] and seen["people"] == ["Dee Dfir"] and seen["editing"] is False
    assert IP not in str(working)

    # Someone with it open right now shows as editing; your own work is never flagged back to you
    connection = uuid.uuid4().hex
    live.hub.presence[theirs["id"]][connection] = live.Viewer(
        str(p["dfir"].id), "Dee Dfir", f"observable:{their_item['id']}", True
    )
    try:
        working = (await client.get(url, headers=p["soc"].headers)).json()
        assert working[0]["visible"][0]["editing"] is True
        # From Dee's side, Sam's SOC incident is one she can't see: counted only
        assert (
            await client.get(f"/api/incidents/{theirs['id']}/observables/working", headers=p["dfir"].headers)
        ).json() == [{"item_id": their_item["id"], "visible": [], "hidden": 1, "ask": ["SOC"]}]
    finally:
        live.hub.presence[theirs["id"]].pop(connection, None)

    assert (await client.get(url, headers=p["dfir"].headers)).status_code == 404
