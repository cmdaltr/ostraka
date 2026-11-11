"""Stakeholder updates against Postgres: drafting from the record, approval by comms, sending through
notify.outward, the scheduler's rhythm, the rephrase check and the observers' briefing."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from ostraka import scheduler
from ostraka.config import get_settings
from ostraka.db import sessions
from ostraka.incidents import activity
from ostraka.models import SecurityEvent, utcnow
from ostraka.updates import ai, drafts
from tests.test_analysis import finding
from tests.test_incidents import declare, people  # noqa: F401  (the fixture)

pytestmark = pytest.mark.db


@pytest.fixture
def email_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "mail.example.org")
    monkeypatch.setattr(settings, "smtp_from", "ostraka@example.org")


async def setup(client, p, **incident):
    """An incident with Lee in comms, a confirmed finding, a lead and an assessment."""
    made = await declare(client, p["soc"], **incident)
    url = f"/api/incidents/{made['id']}"
    r = await client.put(f"{url}/members/{p['lead'].id}", json={"role": "comms"}, headers=p["soc"].headers)
    assert r.status_code == 200, r.text
    staged = await finding(client, p["soc"], made["id"], "Payroll export staged on 203.0.113.9", confirm=True)
    await finding(client, p["soc"], made["id"], "Rclone seen on FIN-WS-07")
    r = await client.post(
        f"{url}/assessments",
        json={
            "judgement": "the actor staged the payroll export for theft",
            "probability": "highly_likely",
            "finding_ids": [staged["id"]],
        },
        headers=p["soc"].headers,
    )
    assert r.status_code == 201, r.text
    return made, url


async def test_a_draft_is_built_from_the_record_and_comms_approves_it(
    db,
    client,
    people,  # noqa: F811
    outbox,
    settle,
    email_on,  # noqa: F811
):
    p = await people()
    made, url = await setup(client, p)
    lead_h, soc_h = p["lead"].headers, p["soc"].headers

    r = await client.post(f"{url}/updates/stakeholders", json={"user_id": str(p["exec"].id)}, headers=lead_h)
    assert r.status_code == 201, r.text
    r = await client.post(
        f"{url}/updates/stakeholders",
        json={"email": "Board@Example.org", "name": "The board"},
        headers=lead_h,
    )
    assert r.status_code == 201
    assert (
        await client.post(f"{url}/updates/stakeholders", json={"email": "board@example.org"}, headers=lead_h)
    ).status_code == 409
    assert (
        await client.post(f"{url}/updates/stakeholders", json={"user_id": str(p["dfir"].id)}, headers=lead_h)
    ).status_code == 409  # can't see the incident
    assert (
        await client.post(
            f"{url}/updates/stakeholders", json={"email": "x@example.org"}, headers=p["dfir"].headers
        )
    ).status_code == 404

    r = await client.post(f"{url}/updates", headers=soc_h)
    assert r.status_code == 201, r.text
    draft = r.json()
    assert (await client.post(f"{url}/updates", headers=soc_h)).status_code == 409  # one draft at a time
    texts = [line["text"] for line in draft["lines"]]
    assert (
        texts[0]
        == "We assess it is highly likely (80–90%) that the actor staged the payroll export for theft."
    )
    whats = [line["text"] for line in draft["lines"] if line["section"] == "what"]
    assert whats[0].startswith("Confirmed: FND-") and "Payroll export staged" in whats[0]
    assert whats[-1].startswith("Lead, not yet confirmed: FND-") and "Rclone" in whats[-1]
    assert draft["flags"] == [] and draft["status"] == "draft"

    # A named comms lead's sign-off can't be skipped by the commander
    r = await client.post(f"{url}/updates/{draft['id']}/approve", json={"version": 1}, headers=soc_h)
    assert r.status_code == 403
    r = await client.patch(
        f"{url}/updates/{draft['id']}", json={"version": 1, "note": "Board call at 17:00."}, headers=lead_h
    )
    assert r.status_code == 200 and r.json()["version"] == 2
    assert (
        await client.post(f"{url}/updates/{draft['id']}/approve", json={"version": 1}, headers=lead_h)
    ).status_code == 409
    r = await client.post(f"{url}/updates/{draft['id']}/approve", json={"version": 2}, headers=lead_h)
    assert r.status_code == 200, r.text
    sent = r.json()
    assert sent["status"] == "sent" and sent["approved_by"]["name"] == "Lee Lead"
    assert {x["channel"]: x["sent"] for x in sent["recipients"]} == {"ostraka": True, "email": True}
    assert [rev["change"] for rev in sent["revisions"]] == ["drafted", "note", "sent"]

    # Immutable once sent
    assert (
        await client.patch(f"{url}/updates/{draft['id']}", json={"version": 3, "note": "x"}, headers=lead_h)
    ).status_code == 409

    await settle()
    mail = next(m for m in outbox.emails if m["To"] == "board@example.org")
    body = mail.get_content()
    assert "203.0.113.9" not in body and "[ip address]" in body and "Board call at 17:00." in body
    assert "Lead, not yet confirmed" in body
    notes = (await client.get("/api/notifications", headers=p["exec"].headers)).json()["items"]
    assert notes[0]["kind"] == "stakeholder_update"
    approved = (
        await db.scalars(select(SecurityEvent).where(SecurityEvent.action == "stakeholder_update_approved"))
    ).all()
    assert len(approved) == 1

    # Observers read the briefing, not the workings
    assert (await client.get(f"{url}/updates", headers=p["exec"].headers)).status_code == 403
    r = await client.get(f"{url}/briefing", headers=p["exec"].headers)
    assert r.status_code == 200, r.text
    brief = r.json()
    assert [u["number"] for u in brief["updates"]] == [1]
    assert brief["confirmed"] == 1 and brief["leads"] == 1 and brief["rhythm"] == "every 4 h"
    shown = " ".join(line["text"] for line in brief["updates"][0]["lines"])
    assert "203.0.113.9" not in shown and "[ip address]" in shown

    # The next draft covers only what changed since
    r = await client.post(f"{url}/updates", headers=soc_h)
    whats = [line["text"] for line in r.json()["lines"] if line["section"] == "what"]
    assert whats == [f"Nothing material has changed since {sent['ref']}."]

    # It's in the briefing export
    r = await client.get(f"{url}/export?variant=briefing&format=docx", headers=soc_h)
    assert r.status_code == 200


async def test_an_update_without_an_assessment_cant_be_approved(db, client, people):  # noqa: F811
    p = await people()
    made = await declare(client, p["soc"])  # no comms: the commander approves
    url = f"/api/incidents/{made['id']}"
    draft = (await client.post(f"{url}/updates", headers=p["soc"].headers)).json()
    assert [f["rule"] for f in draft["flags"] if f["severity"] == "error"] == ["no-assessment"]
    assert draft["can_approve"] is False
    r = await client.post(
        f"{url}/updates/{draft['id']}/approve", json={"version": 1}, headers=p["soc"].headers
    )
    assert r.status_code == 422 and "carries an assessment" in r.json()["detail"]
    assert (
        await client.post(f"{url}/updates", headers=p["dfir"].headers)
    ).status_code == 404  # not on the incident
    r = await client.post(
        f"{url}/updates/{draft['id']}/discard", json={"version": 1}, headers=p["soc"].headers
    )
    assert r.status_code == 204


async def tick(at):
    """The scheduler's update pass alone, so the count is updates acted on (scheduler.tick counts tasks)."""
    async with sessions()() as session:
        acted = await drafts.tick(session, at)
        await activity.finish(session)
        return acted


async def test_the_scheduler_drafts_on_the_rhythm_and_nudges_comms(db, client, people, settle):  # noqa: F811
    p = await people()
    made, url = await setup(client, p, severity="critical")
    assert await tick(utcnow() + timedelta(minutes=30)) == 0
    async with sessions()() as session:  # the scheduler's own tick runs the update pass
        await scheduler.tick(session, now=utcnow() + timedelta(hours=2, minutes=1))
    assert await tick(utcnow() + timedelta(hours=2, minutes=2)) == 0  # once per due time
    await settle()
    state = (await client.get(f"{url}/updates", headers=p["lead"].headers)).json()
    assert state["draft"]["drafted_by"] is None and state["plan"]["words"] == "every 2 h"
    notes = (await client.get("/api/notifications", headers=p["lead"].headers)).json()["items"]
    assert notes[0]["kind"] == "update_due"

    # Comms changes the rhythm for this incident; the commander can too, a responder can't
    plan = state["plan"]
    r = await client.put(
        f"{url}/updates/plan",
        json={"mode": "every", "every_minutes": 60, "version": plan["version"]},
        headers=p["lead"].headers,
    )
    assert r.status_code == 200 and r.json()["plan"]["words"] == "every 1 h"
    assert (
        await client.put(
            f"{url}/updates/plan", json={"mode": "on_change", "version": 1}, headers=p["soc"].headers
        )
    ).status_code == 409  # stale version


async def test_a_low_incident_is_drafted_only_when_something_changes(db, client, people):  # noqa: F811
    p = await people()
    made = await declare(client, p["soc"], severity="low")
    url = f"/api/incidents/{made['id']}"
    await client.post(f"{url}/updates", headers=p["soc"].headers)  # the first, by hand
    first = (await client.get(f"{url}/updates", headers=p["soc"].headers)).json()["draft"]
    await client.post(f"{url}/updates/{first['id']}/discard", json={"version": 1}, headers=p["soc"].headers)
    assert await tick(utcnow() + timedelta(days=3)) == 0
    await finding(client, p["soc"], made["id"], "Suspicious login from a new country")
    assert await tick(utcnow() + timedelta(minutes=1)) == 1
    draft = (await client.get(f"{url}/updates", headers=p["soc"].headers)).json()["draft"]
    assert any(line["text"].startswith("Lead, not yet confirmed") for line in draft["lines"])


async def test_an_ai_rewording_is_used_only_if_it_keeps_every_claim(db, client, people, monkeypatch):  # noqa: F811
    p = await people()
    made, url = await setup(client, p)
    lead_h = p["lead"].headers
    draft = (await client.post(f"{url}/updates", headers=lead_h)).json()

    monkeypatch.setattr(ai, "provider_for", lambda incident: "ollama")
    sent_to_model = []

    async def drops_the_lead(provider, prompt):
        sent_to_model.append(prompt)
        lines = [line["text"] for line in draft["lines"]]
        lines = [t.replace("Lead, not yet confirmed: ", "") for t in lines]
        return json.dumps(lines)

    monkeypatch.setattr(ai, "complete", drops_the_lead)
    r = await client.post(f"{url}/updates/{draft['id']}/rephrase", json={"version": 1}, headers=lead_h)
    assert r.status_code == 200 and r.json()["accepted"] is False
    assert any("strongly" in x or "references" in x for x in r.json()["problems"])
    assert r.json()["update"]["use_rephrased"] is False and r.json()["update"]["version"] == 1
    assert "203.0.113.9" not in sent_to_model[0]  # the model sees indicators masked

    async def rewords(provider, prompt):
        lines = [line["text"].replace("203.0.113.9", "[ip address]") for line in draft["lines"]]
        lines[0] = lines[0].replace(
            "staged the payroll export for theft", "gathered the payroll export to steal it"
        )
        return json.dumps(lines)

    monkeypatch.setattr(ai, "complete", rewords)
    r = await client.post(f"{url}/updates/{draft['id']}/rephrase", json={"version": 1}, headers=lead_h)
    assert r.json()["accepted"] is True, r.json()["problems"]
    update = r.json()["update"]
    assert update["use_rephrased"] is True and update["rephrased_by"] == "ollama"
    assert "gathered the payroll export" in update["lines"][0]["rephrased"]

    r = await client.post(
        f"{url}/updates/{draft['id']}/approve", json={"version": update["version"]}, headers=lead_h
    )
    assert r.status_code == 200
    brief = (await client.get(f"{url}/briefing", headers=p["exec"].headers)).json()
    assert brief["updates"][0]["ai_worded"] is True
    assert "gathered the payroll export" in brief["updates"][0]["lines"][0]["text"]


async def test_the_tlp_guard_refuses_a_rewording_it_cant_keep_local(db, client, people, monkeypatch):  # noqa: F811
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(settings, "anthropic_model", "a-model")
    monkeypatch.setattr(settings, "ollama_model", "")
    p = await people()
    made, url = await setup(client, p, tlp="red")
    draft = (await client.post(f"{url}/updates", headers=p["lead"].headers)).json()
    state = (await client.get(f"{url}/updates", headers=p["lead"].headers)).json()
    assert state["ai"] == "" and "Ollama" in state["ai_why_not"] and state["restricted"] is True
    r = await client.post(
        f"{url}/updates/{draft['id']}/rephrase", json={"version": 1}, headers=p["lead"].headers
    )
    assert r.status_code == 409 and "TLP:RED" in r.json()["detail"]
