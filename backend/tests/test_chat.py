"""Chat channels in Teams and Slack: a team's, and an incident's own."""

import pytest
from sqlalchemy import select

from ostraka.models import ActivityEvent, Incident, SecurityEvent, Team
from tests.test_incidents import declare, people, team_id  # noqa: F401  (people is a fixture)

pytestmark = pytest.mark.db

SLACK = "https://hooks.slack.com/services/T0001/B0001/secretpart"
TEAMS = "https://prod-01.westeurope.logic.azure.com/workflows/abc/triggers/manual/paths/invoke?sig=secret"


async def test_a_team_channel_can_be_slack(db, client, people, outbox, settle):  # noqa: F811
    p = await people()
    soc = await team_id(db, "SOC")
    r = await client.put(f"/api/teams/{soc}/channel", json={"url": SLACK}, headers=p["lead"].headers)
    assert r.status_code == 200, r.text
    assert r.json()["has_channel"] is True and r.json()["channel_kind"] == "slack"
    assert "secretpart" not in r.text
    assert "secretpart" not in await db.scalar(select(Team.channel_webhook).where(Team.name == "SOC"))

    assert (await client.post(f"/api/teams/{soc}/channel/test", headers=p["lead"].headers)).status_code == 204
    assert "connected to SOC" in outbox.slack()[-1]["blocks"][0]["text"]["text"]

    incident = await declare(client, p["soc"])
    task = await client.post(
        f"/api/incidents/{incident['id']}/tasks",
        json={"title": "Pull the mailbox audit log", "team_id": soc},
        headers=p["soc"].headers,
    )
    assert task.status_code == 201, task.text
    await settle()
    assert any(task.json()["ref"] in m["text"] for m in outbox.slack())

    audited = await db.scalar(select(SecurityEvent).where(SecurityEvent.action == "team_channel_set"))
    assert audited is not None and audited.detail.get("kind") == "slack"


async def test_an_incident_channel_is_for_whoever_manages_the_incident(db, client, people, outbox):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}/channel"
    assert (await client.put(base, json={"url": SLACK}, headers=p["dfir"].headers)).status_code == 404
    assert (await client.put(base, json={"url": SLACK}, headers=p["exec"].headers)).status_code == 403
    assert (
        await client.put(base, json={"url": "https://10.0.0.5/x"}, headers=p["soc"].headers)
    ).status_code == 422
    assert (await client.post(f"{base}/test", headers=p["soc"].headers)).status_code == 409

    r = await client.put(base, json={"url": TEAMS}, headers=p["soc"].headers)
    assert r.status_code == 200 and r.json()["channel_kind"] == "teams" and "sig=secret" not in r.text
    assert "sig=secret" not in await db.scalar(
        select(Incident.channel_webhook).where(Incident.id == incident["id"])
    )
    assert (await client.post(f"{base}/test", headers=p["soc"].headers)).status_code == 204
    assert "Ostraka is connected" in outbox.teams()[-1]["attachments"][0]["content"]["body"][0]["text"]

    said = await db.scalar(
        select(ActivityEvent.summary).where(
            ActivityEvent.incident_id == incident["id"], ActivityEvent.action == "channel_set"
        )
    )
    assert said == "Sam Soc connected a Microsoft Teams channel"

    cleared = await client.put(base, json={"url": ""}, headers=p["soc"].headers)
    assert cleared.json()["channel_kind"] == ""


async def test_an_incident_channel_hears_its_turns_and_a_red_one_only_its_name(
    db,
    client,
    people,  # noqa: F811
    outbox,
    settle,
):
    p = await people()
    incident = await declare(client, p["soc"])
    url = f"/api/incidents/{incident['id']}"
    await client.put(f"{url}/channel", json={"url": SLACK}, headers=p["soc"].headers)
    await settle()
    before = len(outbox.slack())

    edited = await client.patch(
        url, json={"title": "New title", "version": incident["version"]}, headers=p["soc"].headers
    )
    assert edited.status_code == 200, edited.text
    await settle()
    assert len(outbox.slack()) == before  # an edit isn't a turn

    moved = await client.patch(
        url, json={"phase": "scope", "version": edited.json()["version"]}, headers=p["soc"].headers
    )
    assert moved.status_code == 200, moved.text
    await settle()
    post = outbox.slack()[-1]
    assert (
        post["blocks"][0]["text"]["text"] == f"The phase changed in {incident['ref']} {incident['codename']}"
    )
    assert "detect → scope" in post["blocks"][1]["text"]["text"]

    red = await client.patch(
        url, json={"tlp": "red", "version": moved.json()["version"]}, headers=p["soc"].headers
    )
    assert red.status_code == 200, red.text
    await client.patch(
        url, json={"phase": "contain", "version": red.json()["version"]}, headers=p["soc"].headers
    )
    await settle()
    post = outbox.slack()[-1]
    assert post["blocks"][0]["text"]["text"].startswith("The phase changed in")
    assert "New title" not in str(post) and "open Ostraka" in post["blocks"][1]["text"]["text"]
