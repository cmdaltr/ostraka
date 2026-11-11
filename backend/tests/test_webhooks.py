"""Webhooks against Postgres: kept but quiet on Free, sent and signed on Pro, cut down for restricted
incidents, and only ever sent after the change they describe was committed."""

import json

import pytest
from sqlalchemy import select

from ostraka import webhooks
from ostraka.models import Incident, SecurityEvent
from ostraka.notify import channels
from tests.test_incidents import declare, people  # noqa: F401  (the fixture)
from tests.test_licence import accepted

pytestmark = pytest.mark.db

HOOK = "https://hooks.example.org/ostraka"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(webhooks, "BACKOFF", 0)


async def register(client, admin, events=("incident.created",), **extra) -> dict:
    body = {"url": HOOK, "events": list(events), **extra}
    r = await client.post("/api/webhooks", json=body, headers=admin.headers)
    assert r.status_code == 201, r.text
    return r.json()


async def install(client, admin, licence_line, edition="pro"):
    r = await client.put("/api/licence", json=accepted(licence_line(edition)), headers=admin.headers)
    assert r.status_code == 200, r.text


def sent(outbox) -> list[tuple[dict, dict[str, str], bytes]]:
    return [(json.loads(body), headers, body) for url, body, headers in outbox.posts if url == HOOK]


async def test_on_free_webhooks_are_kept_but_not_sent(db, client, people, outbox):  # noqa: F811
    p = await people()
    hook = await register(client, p["admin"])
    assert hook["secret"] and hook["has_secret"]
    await declare(client, p["soc"])
    await webhooks.wait()

    assert sent(outbox) == []
    listed = (await client.get("/api/webhooks", headers=p["admin"].headers)).json()
    assert listed["sending"] is False and [h["id"] for h in listed["webhooks"]] == [hook["id"]]
    assert "secret" not in listed["webhooks"][0] or listed["webhooks"][0]["secret"] is None
    r = await client.post(f"/api/webhooks/{hook['id']}/test", headers=p["admin"].headers)
    assert r.status_code == 402 and "Pro and Services" in r.json()["detail"]


async def test_on_pro_an_event_is_sent_signed_and_recorded(db, client, people, outbox, licence_line):  # noqa: F811
    p = await people()
    await install(client, p["admin"], licence_line)
    hook = await register(client, p["admin"], events=("incident.created", "incident.closed"))
    incident = await declare(client, p["soc"], title="Beaconing to 203.0.113.9")
    await webhooks.wait()

    [(body, headers, raw)] = sent(outbox)
    assert body["event"] == "incident.created" and headers["X-Ostraka-Event"] == "incident.created"
    assert headers["X-Ostraka-Signature"] == webhooks.sign(hook["secret"], raw)
    data = body["data"]
    assert data["ref"] == incident["ref"] and data["severity"] == "high" and data["phase"] == "detect"
    assert data["title"] == "Beaconing to [ip address]"  # masked like every outward message
    assert data["url"].endswith(f"/incidents/{incident['ref']}")

    listed = (await client.get("/api/webhooks", headers=p["admin"].headers)).json()
    assert listed["sending"] is True
    [delivery] = listed["webhooks"][0]["deliveries"]
    assert delivery["ok"] and delivery["status"] == 201 and delivery["event"] == "incident.created"


async def test_closing_and_assigning_send_their_events(db, client, people, outbox, licence_line):  # noqa: F811
    from tests.test_work import add_task

    p = await people()
    await install(client, p["admin"], licence_line)
    await register(client, p["admin"], events=webhooks.EVENTS)
    incident = await declare(client, p["soc"])
    await add_task(client, p["soc"], incident, title="Pull the audit log", assignee_id=str(p["soc"].id))
    r = await client.patch(
        f"/api/incidents/{incident['id']}",
        json={"version": incident["version"], "status": "closed"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text
    await webhooks.wait()

    events = [body["event"] for body, _, _ in sent(outbox)]
    assert events == ["incident.created", "task.assigned", "incident.closed"]
    task = sent(outbox)[1][0]["data"]["task"]
    assert task["title"] == "Pull the audit log" and task["assignee"] == "Sam Soc"


async def test_a_restricted_incident_sends_its_reference_only(db, client, people, outbox, licence_line):  # noqa: F811
    from tests.test_work import add_task

    p = await people()
    await install(client, p["admin"], licence_line)
    await register(client, p["admin"], events=("incident.created", "task.assigned"))
    red = await declare(client, p["soc"], title="Board member's account taken over", tlp="red")
    await add_task(client, p["soc"], red, title="Reset the board member's MFA", assignee_id=str(p["soc"].id))
    await webhooks.wait()

    bodies = [body for body, _, _ in sent(outbox)]
    assert [b["event"] for b in bodies] == ["incident.created", "task.assigned"]
    assert all(b["data"] == {"ref": red["ref"]} for b in bodies)
    assert "board" not in json.dumps(bodies).lower()


async def test_webhooks_go_after_the_commit_and_never_for_a_rolled_back_change(
    db,
    client,
    people,  # noqa: F811
    outbox,
    licence_line,
    monkeypatch,  # noqa: F811
):
    from ostraka.db import sessions

    p = await people()
    await install(client, p["admin"], licence_line)
    await register(client, p["admin"])
    committed: list[bool] = []
    original = channels.fetch

    async def fetch(url, *, content, headers):
        if url == HOOK:  # by the time a receiver hears about it, the incident is there to fetch
            ref = json.loads(content)["data"]["ref"]
            async with sessions()() as other:
                rows = (await other.scalars(select(Incident))).all()
                committed.append(any(i.ref == ref for i in rows))
        return await original(url, content=content, headers=headers)

    monkeypatch.setattr(channels, "fetch", fetch)
    # Declaring with a playbook that doesn't exist fails after the activity was recorded
    r = await client.post(
        "/api/incidents",
        json={"title": "Never happened", "severity": "low", "playbook_ids": [str(p["admin"].id)]},
        headers=p["soc"].headers,
    )
    assert r.status_code == 404
    await declare(client, p["soc"])
    await webhooks.wait()

    assert committed == [True]
    assert [b["data"]["title"] for b, _, _ in sent(outbox)] == ["Mailbox rule forwarding finance mail"]


async def test_a_failing_receiver_is_tried_three_times_and_a_refusal_once(
    db,
    client,
    people,  # noqa: F811
    outbox,
    licence_line,
):
    p = await people()
    await install(client, p["admin"], licence_line)
    hook = await register(client, p["admin"])
    outbox.status = 503
    await declare(client, p["soc"])
    await webhooks.wait()
    outbox.status = 410
    await declare(client, p["soc"], title="Second")
    await webhooks.wait()

    deliveries = (await client.get("/api/webhooks", headers=p["admin"].headers)).json()["webhooks"][0]
    assert [(d["tries"], d["error"], d["ok"]) for d in deliveries["deliveries"]] == [
        (1, "HTTP 410", False),
        (3, "HTTP 503", False),
    ]
    assert len(sent(outbox)) == 4 and deliveries["id"] == hook["id"]


async def test_only_the_newest_deliveries_are_kept(db, client, people, outbox, licence_line, monkeypatch):  # noqa: F811
    monkeypatch.setattr(webhooks, "KEEP_DELIVERIES", 2)
    p = await people()
    await install(client, p["admin"], licence_line)
    await register(client, p["admin"])
    for n in range(3):
        await declare(client, p["soc"], title=f"Incident {n}")
        await webhooks.wait()
    hook = (await client.get("/api/webhooks", headers=p["admin"].headers)).json()["webhooks"][0]
    assert len(hook["deliveries"]) == 2


async def test_webhooks_are_for_admins_checked_and_audited(db, client, people, outbox, licence_line):  # noqa: F811
    p = await people()
    for who in ("lead", "soc"):
        r = await client.post(
            "/api/webhooks", json={"url": HOOK, "events": ["incident.created"]}, headers=p[who].headers
        )
        assert r.status_code == 403
    r = await client.post(
        "/api/webhooks",
        json={"url": "http://hooks.example.org/x", "events": ["incident.created"]},
        headers=p["admin"].headers,
    )
    assert r.status_code == 422 and "https://" in r.json()["detail"]
    r = await client.post(
        "/api/webhooks", json={"url": HOOK, "events": ["run.completed"]}, headers=p["admin"].headers
    )
    assert r.status_code == 422

    hook = await register(client, p["admin"])
    r = await client.patch(
        f"/api/webhooks/{hook['id']}", json={"version": 1, "enabled": False}, headers=p["admin"].headers
    )
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["version"] == 2
    stale = await client.patch(
        f"/api/webhooks/{hook['id']}", json={"version": 1, "enabled": True}, headers=p["admin"].headers
    )
    assert stale.status_code == 409
    await install(client, p["admin"], licence_line)
    await declare(client, p["soc"])  # disabled: not sent
    await webhooks.wait()
    assert sent(outbox) == []

    r = await client.post(f"/api/webhooks/{hook['id']}/test", headers=p["admin"].headers)
    assert r.status_code == 200 and r.json()["ok"]
    assert (await client.delete(f"/api/webhooks/{hook['id']}", headers=p["admin"].headers)).status_code == 204
    actions = (await db.scalars(select(SecurityEvent.action).order_by(SecurityEvent.at))).all()
    assert [a for a in actions if a.startswith("webhook_")] == [
        "webhook_added",
        "webhook_changed",
        "webhook_removed",
    ]
