"""The attack flow end to end against Postgres: built from the record's findings and tasks, an analyst's
calls on stages, attribution to an ATT&CK group, and who may change what."""

import pytest
from sqlalchemy import select

from ostraka.models import ActivityEvent
from tests.test_handover import add_task
from tests.test_incidents import declare, people, team_id  # noqa: F401

pytestmark = pytest.mark.db


async def flow_of(client, person, incident) -> dict:
    r = await client.get(f"/api/incidents/{incident['id']}/flow", headers=person.headers)
    assert r.status_code == 200, r.text
    return r.json()


def stage(data: dict, key: str) -> dict:
    return next(s for s in data["stages"] if s["key"] == key)


async def test_the_flow_is_built_from_findings_and_covered_by_calls(db, client, people):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    r = await client.post(
        f"{base}/findings",
        json={"kind": "evidence", "title": "Invoice attachment opened", "techniques": ["T1566.001"]},
        headers=p["soc"].headers,
    )
    assert r.status_code == 201, r.text

    data = await flow_of(client, p["soc"], incident)
    gets_in = stage(data, "gets_in")
    assert gets_in["layer"] == "seen" and gets_in["coverage"] == "focused"  # a lead, not yet confirmed
    assert gets_in["techniques"] == [{"id": "T1566.001", "name": "Spearphishing Attachment"}]
    expected = [s for s in data["stages"] if s["layer"] == "expected"]
    assert expected and all("ATT&CK groups most like this record" in s["why"] for s in expected)
    assert data["covered"] == 0 and data["can_edit"] is True

    # a task for a stage keeps it focused until it's done
    target = expected[0]["key"]
    await add_task(
        client,
        p["soc"],
        incident,
        title="Check the proxy logs",
        stage=target,
        team_id=await team_id(db, "SOC"),
    )
    assert (
        stage(await flow_of(client, p["soc"], incident), target)["tasks_open"][0]["title"]
        == "Check the proxy logs"
    )

    # checked, nothing found: covered once the task is done too
    r = await client.put(
        f"{base}/flow/stages/{target}",
        json={"call": "cleared", "note": "Proxy logs 10–14 Nov, nothing"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 204, r.text
    assert stage(await flow_of(client, p["soc"], incident), target)["coverage"] == "focused"

    # ruled out: it leaves the flow and is listed with the reason
    r = await client.put(
        f"{base}/flow/stages/impact",
        json={"call": "dismissed", "note": "No encryption anywhere"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 204
    data = await flow_of(client, p["soc"], incident)
    assert "impact" not in [s["key"] for s in data["stages"]]
    assert (
        data["dismissed"][0]["note"] == "No encryption anywhere" and data["dismissed"][0]["by"] == "Sam Soc"
    )

    # withdrawn, it's read from the record again
    assert (await client.delete(f"{base}/flow/stages/impact", headers=p["soc"].headers)).status_code == 204
    assert (await flow_of(client, p["soc"], incident))["dismissed"] == []

    summaries = (
        await db.scalars(select(ActivityEvent.summary).where(ActivityEvent.action == "flow_stage"))
    ).all()
    assert any("checked and found nothing in" in s for s in summaries)
    assert any(s.startswith("Sam Soc withdrew the call on Does damage") for s in summaries)


async def test_attribution_shapes_the_expectations(db, client, people):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    await client.post(
        f"{base}/findings",
        json={"kind": "evidence", "title": "Spearphish", "techniques": ["T1566.001"]},
        headers=p["soc"].headers,
    )
    groups = (await client.get("/api/attack/groups", headers=p["soc"].headers)).json()
    assert {"id": "G0016", "name": "APT29"} in groups

    assert (
        await client.put(f"{base}/flow/attribution", json={"group": "G9999"}, headers=p["soc"].headers)
    ).status_code == 422
    assert (
        await client.put(f"{base}/flow/attribution", json={"group": "g0016"}, headers=p["soc"].headers)
    ).status_code == 204
    data = await flow_of(client, p["soc"], incident)
    assert data["group"] == {"id": "G0016", "name": "APT29"} and data["peers"] == 1
    assert all(
        s["why"] == "APT29 (G0016) is known to reach this stage."
        for s in data["stages"]
        if s["layer"] == "expected"
    )


async def test_observers_read_the_flow_but_make_no_calls(db, client, people):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    data = await flow_of(client, p["exec"], incident)
    assert data["can_edit"] is False
    r = await client.put(
        f"{base}/flow/stages/impact", json={"call": "dismissed", "note": "No"}, headers=p["exec"].headers
    )
    assert r.status_code in (403, 422)
    r = await client.put(
        f"{base}/flow/stages/impact",
        json={"call": "dismissed", "note": "Not here"},
        headers=p["exec"].headers,
    )
    assert r.status_code == 403
    assert (await client.get(f"{base}/flow", headers=p["dfir"].headers)).status_code == 404
    r = await client.put(
        f"{base}/flow/stages/nowhere", json={"call": "cleared", "note": "Looked"}, headers=p["soc"].headers
    )
    assert r.status_code == 404
