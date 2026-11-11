"""Regulator clocks against Postgres: adding and changing them, who may, marking milestones met, the
nudges and what they send, and the work page's deadlines."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update

from ostraka import scheduler
from ostraka.config import get_settings
from ostraka.db import sessions
from ostraka.models import SecurityEvent, User, utcnow
from tests.test_work import people  # noqa: F401  (the fixture)

pytestmark = pytest.mark.db


async def declare(client, person, **body) -> dict:
    r = await client.post(
        "/api/incidents",
        json={"title": "Payroll share encrypted", "severity": "high", "tlp": "amber", **body},
        headers=person.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def add_clock(client, person, incident, expect=201, **body) -> dict:
    r = await client.post(
        f"/api/incidents/{incident['id']}/clocks", json={"regime": "uk_gdpr", **body}, headers=person.headers
    )
    assert r.status_code == expect, r.text
    return r.json()


async def make_comms(client, commander, incident, person):
    r = await client.put(
        f"/api/incidents/{incident['id']}/members/{person.id}",
        json={"role": "comms"},
        headers=commander.headers,
    )
    assert r.status_code == 200, r.text


async def kinds(client, person) -> list[str]:
    # The same ticks fall due for stakeholder updates (tests/test_updates.py); these tests are about clocks
    r = await client.get("/api/notifications", headers=person.headers)
    return [n["kind"] for n in r.json()["items"] if n["kind"] != "update_due"]


async def activity(client, person, incident) -> list[dict]:
    r = await client.get(f"/api/incidents/{incident['id']}/activity", headers=person.headers)
    return [e for e in r.json() if e["entity_type"] == "clock"]


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


async def test_a_commander_starts_a_clock_with_an_owner_and_it_counts_from_awareness(
    db,
    client,
    people,  # noqa: F811
    outbox,
    settle,
):
    p = await people()
    aware = (utcnow() - timedelta(hours=10)).replace(microsecond=0)
    incident = await declare(client, p["soc"], aware_at=aware.isoformat())
    clock = await add_clock(client, p["soc"], incident, owner_id=str(p["soc2"].id))
    assert clock["ref"].startswith("CLK-") and clock["regime_name"] == "UK GDPR"
    assert clock["owner"]["name"] == "Sky Soc" and clock["timezone"] == "Europe/London"
    (report,) = clock["milestones"]
    assert report["key"] == "report" and at(report["due_at"]) == aware + timedelta(hours=72)
    assert report["state"] == "running" and report["counted"] == "72 h from awareness"
    assert clock["can_manage"] and clock["can_mark"]

    await settle()
    assert await kinds(client, p["soc2"]) == ["clock_due"]
    (event,) = await activity(client, p["soc"], incident)
    assert event["action"] == "clock_added" and "owned by Sky Soc" in event["summary"]
    logged = (await db.scalars(select(SecurityEvent).where(SecurityEvent.action == "clock_added"))).all()
    assert len(logged) == 1 and logged[0].detail["clock"] == clock["ref"]

    # Correcting when we became aware moves the deadline with it
    r = await client.patch(
        f"/api/incidents/{incident['id']}",
        json={"version": incident["version"], "aware_at": (aware - timedelta(hours=5)).isoformat()},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text
    (listed,) = (
        await client.get(f"/api/incidents/{incident['id']}/clocks", headers=p["exec"].headers)
    ).json()
    assert at(listed["milestones"][0]["due_at"]) == aware + timedelta(hours=67)
    assert not listed["can_manage"] and not listed["can_mark"]  # the observer sees it, read only


async def test_only_commander_comms_or_an_admin_add_and_close_clocks(db, client, people, outbox):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    await client.put(
        f"/api/incidents/{incident['id']}/members/{p['soc2'].id}", json={}, headers=p["soc"].headers
    )
    r = await add_clock(client, p["soc2"], incident, expect=403)
    assert "commander, comms" in r["detail"]
    await add_clock(client, p["exec"], incident, expect=403)
    await add_clock(client, p["lead"], incident, expect=403)  # leading the team isn't a role in the incident
    await make_comms(client, p["soc"], incident, p["soc2"])
    clock = await add_clock(client, p["soc2"], incident)
    assert clock["owner"]["name"] == "Sky Soc"  # whoever adds it, unless they say
    await add_clock(client, p["admin"], incident, regime="uk_nis")

    # The owner can't be an observer or someone who can't see the incident
    r = await add_clock(client, p["soc"], incident, expect=422, owner_id=str(p["exec"].id))
    assert "Observers" in r["detail"]
    await add_clock(client, p["soc"], incident, expect=422, owner_id=str(p["dfir"].id))

    # Hunts don't take clocks
    hunt = await declare(client, p["soc"], kind="hunt", severity="low")
    r = await add_clock(client, p["soc"], hunt, expect=422)
    assert "not hunts" in r["detail"]

    url = f"/api/incidents/{incident['id']}/clocks/{clock['id']}"
    reason = {"version": clock["version"], "reason": "Not reportable: no personal data was in the share"}
    assert (await client.post(f"{url}/close", json=reason, headers=p["lead"].headers)).status_code == 403
    r = await client.post(f"{url}/close", json=reason, headers=p["soc"].headers)
    assert r.status_code == 200 and r.json()["closed_by"]["name"] == "Sam Soc" and not r.json()["can_mark"]
    closed = r.json()
    r = await client.post(
        f"{url}/milestones/report/met", json={"version": closed["version"]}, headers=p["soc"].headers
    )
    assert r.status_code == 409 and "reopen" in r.json()["detail"]
    r = await client.post(f"{url}/reopen", json={"version": closed["version"]}, headers=p["soc"].headers)
    assert r.status_code == 200 and r.json()["closed_at"] is None
    actions = [e["action"] for e in await activity(client, p["soc"], incident)]
    assert actions.count("clock_added") == 2 and "clock_closed" in actions and "clock_reopened" in actions


async def test_marking_a_milestone_met_records_who_when_and_the_reference(db, client, people, outbox):  # noqa: F811
    p = await people()
    aware = utcnow() - timedelta(hours=80)
    incident = await declare(client, p["soc"], aware_at=aware.isoformat())
    await client.put(
        f"/api/incidents/{incident['id']}/members/{p['soc2'].id}", json={}, headers=p["soc"].headers
    )
    clock = await add_clock(client, p["soc"], incident, owner_id=str(p["soc2"].id))
    assert clock["milestones"][0]["state"] == "breached"
    url = f"/api/incidents/{incident['id']}/clocks/{clock['id']}"

    # The owner may mark it met, though they can't manage the clock
    r = await client.patch(url, json={"version": clock["version"], "title": "x"}, headers=p["soc2"].headers)
    assert r.status_code == 403
    early = (aware - timedelta(minutes=5)).isoformat()
    body = {"version": clock["version"], "reference": "ICO case IC-412873", "met_at": early}
    r = await client.post(f"{url}/milestones/report/met", json=body, headers=p["soc2"].headers)
    assert r.status_code == 422 and "before you became aware" in r.json()["detail"]
    future = (utcnow() + timedelta(hours=1)).isoformat()
    r = await client.post(
        f"{url}/milestones/report/met", json={**body, "met_at": future}, headers=p["soc2"].headers
    )
    assert r.status_code == 422
    submitted = aware + timedelta(hours=70)
    r = await client.post(
        f"{url}/milestones/report/met",
        json={**body, "met_at": submitted.isoformat(), "note": "Phoned, then the online form"},
        headers=p["soc2"].headers,
    )
    assert r.status_code == 200, r.text
    met = r.json()["milestones"][0]
    assert met["state"] == "met" and met["met_by"]["name"] == "Sky Soc"
    assert met["reference"] == "ICO case IC-412873" and at(met["met_at"]) == submitted
    assert r.json()["version"] == clock["version"] + 1

    # Stale versions and doubles are refused
    r = await client.post(f"{url}/milestones/report/met", json=body, headers=p["soc2"].headers)
    assert r.status_code == 409
    r = await client.post(
        f"{url}/milestones/report/met",
        json={**body, "version": clock["version"] + 1},
        headers=p["soc2"].headers,
    )
    assert r.status_code == 409 and "already" in r.json()["detail"]
    r = await client.post(
        f"{url}/milestones/nope/met", json={"version": clock["version"] + 1}, headers=p["soc"].headers
    )
    assert r.status_code == 404

    events = await activity(client, p["soc"], incident)
    assert events[-1]["action"] == "clock_milestone_met"
    assert "in time (reference ICO case IC-412873)" in events[-1]["summary"]
    logged = (
        await db.scalars(select(SecurityEvent).where(SecurityEvent.action == "clock_milestone_met"))
    ).one()
    assert logged.actor_id == p["soc2"].id and logged.detail["reference"] == "ICO case IC-412873"

    # Taking it back is recorded too, and the deadline is breached again
    r = await client.post(
        f"{url}/milestones/report/unmet", json={"version": clock["version"] + 1}, headers=p["soc"].headers
    )
    assert r.status_code == 200 and r.json()["milestones"][0]["state"] == "breached"
    assert (await activity(client, p["soc"], incident))[-1]["summary"].endswith("(was ICO case IC-412873)")


async def test_dora_tightens_on_classification_and_later_reports_count_from_the_one_before(
    db,
    client,
    people,  # noqa: F811
    outbox,
):
    p = await people()
    aware = (utcnow() - timedelta(hours=6)).replace(microsecond=0)
    incident = await declare(client, p["soc"], aware_at=aware.isoformat())
    clock = await add_clock(client, p["soc"], incident, regime="dora")
    assert clock["needs_classification"] and clock["timezone"] == "Europe/Brussels"
    initial, intermediate, final = clock["milestones"]
    assert at(initial["due_at"]) == aware + timedelta(hours=24)
    assert intermediate["provisional"] and final["provisional"]

    url = f"/api/incidents/{incident['id']}/clocks/{clock['id']}"
    classified = aware + timedelta(hours=1)
    r = await client.patch(
        url,
        json={"version": clock["version"], "classified_at": classified.isoformat()},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text
    clock = r.json()
    assert at(clock["milestones"][0]["due_at"]) == classified + timedelta(hours=4)
    assert clock["milestones"][0]["state"] == "breached"

    sent = aware + timedelta(hours=5, minutes=30)
    r = await client.post(
        f"{url}/milestones/initial/met",
        json={"version": clock["version"], "met_at": sent.isoformat()},
        headers=p["soc"].headers,
    )
    clock = r.json()
    assert clock["milestones"][0]["state"] == "met_late"
    assert at(clock["milestones"][1]["due_at"]) == sent + timedelta(hours=72)
    assert not clock["milestones"][1]["provisional"] and clock["milestones"][2]["provisional"]
    r = await client.patch(
        url,
        json={"version": clock["version"], "milestones": [{"name": "x", "amount": 1, "unit": "hours"}]},
        headers=p["soc"].headers,
    )
    assert r.status_code == 422 and "fixed" in r.json()["detail"]


async def test_custom_clocks_take_their_own_milestones(db, client, people, outbox):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    insurer = [
        {"name": "Tell the insurer", "amount": 48, "unit": "hours"},
        {"name": "Proof of loss", "amount": 30, "unit": "days", "basis": "previous"},
    ]
    r = await add_clock(client, p["soc"], incident, expect=422, regime="custom", milestones=insurer)
    assert "Name a custom clock" in r["detail"]
    await add_clock(client, p["soc"], incident, expect=422, regime="custom", title="Board")
    await add_clock(client, p["soc"], incident, expect=422, milestones=insurer)  # UK GDPR's are fixed
    clock = await add_clock(
        client, p["soc"], incident, regime="custom", title="Beazley cyber policy", milestones=insurer
    )
    assert [m["key"] for m in clock["milestones"]] == ["m1", "m2"]
    assert clock["custom_milestones"][1]["basis"] == "previous"
    url = f"/api/incidents/{incident['id']}/clocks/{clock['id']}"

    r = await client.post(
        f"{url}/milestones/m2/met", json={"version": clock["version"]}, headers=p["soc"].headers
    )
    clock = r.json()
    r = await client.patch(
        url, json={"version": clock["version"], "milestones": insurer[:1]}, headers=p["soc"].headers
    )
    assert r.status_code == 409 and "already met" in r.json()["detail"]
    three = [*insurer, {"name": "Final claim", "amount": 3, "unit": "months", "basis": "previous"}]
    r = await client.patch(
        url, json={"version": clock["version"], "milestones": three}, headers=p["soc"].headers
    )
    assert r.status_code == 200 and [m["key"] for m in r.json()["milestones"]] == ["m1", "m2", "m3"]
    assert r.json()["milestones"][1]["state"] == "met"  # the met one kept its record


# ---- nudges ---------------------------------------------------------------------------------------------


async def tick(now):
    async with sessions()() as session:
        return await scheduler.tick(session, now=now)


async def test_the_owner_then_the_commander_hear_as_the_deadline_approaches(
    db,
    client,
    people,  # noqa: F811
    outbox,
    settle,
):
    p = await people()
    aware = utcnow() - timedelta(minutes=1)
    incident = await declare(client, p["soc"], aware_at=aware.isoformat())
    await client.put(
        f"/api/incidents/{incident['id']}/members/{p['soc2'].id}", json={}, headers=p["soc"].headers
    )
    await add_clock(client, p["soc"], incident, owner_id=str(p["soc2"].id))
    await settle()
    hours = lambda h: aware + timedelta(hours=h)  # noqa: E731

    assert await tick(hours(30)) == 0
    assert await tick(hours(37)) == 1  # half the time gone: the owner
    assert await tick(hours(38)) == 0  # once
    await settle()
    assert await kinds(client, p["soc2"]) == ["clock_due", "clock_due"]
    assert await kinds(client, p["soc"]) == []
    assert await tick(hours(55)) == 1  # three quarters: still the owner alone
    await settle()
    assert await kinds(client, p["soc"]) == []
    assert await tick(hours(65)) == 1  # nine tenths: the commander as well
    await settle()
    assert await kinds(client, p["soc"]) == ["clock_due"]
    notes = (await client.get("/api/notifications", headers=p["soc"].headers)).json()["items"]
    notes = [n for n in notes if n["kind"] != "update_due"]
    assert "due in 7 h (90% of the time gone)" in notes[0]["title"]
    assert "as incident commander" in notes[0]["body"]
    assert notes[0]["link"].endswith("?s=clocks#" + notes[0]["link"].split("#")[1])

    assert await tick(hours(73)) == 1  # the breach
    assert await tick(hours(90)) == 0
    await settle()
    assert (await kinds(client, p["soc2"]))[0] == "clock_breached"
    assert (await kinds(client, p["soc"]))[0] == "clock_breached"
    breach = (await activity(client, p["soc"], incident))[-1]
    assert breach["action"] == "clock_breached" and breach["actor"] is None
    assert "Told: Sky Soc, Sam Soc" in breach["summary"]


async def test_a_late_scheduler_sends_one_nudge_and_met_or_closed_clocks_send_none(
    db,
    client,
    people,  # noqa: F811
    outbox,
    settle,
):
    p = await people()
    aware = utcnow() - timedelta(minutes=1)
    incident = await declare(client, p["soc"], aware_at=aware.isoformat())
    gdpr = await add_clock(client, p["soc"], incident)
    nis = await add_clock(client, p["soc"], incident, regime="uk_nis")
    nis2 = await add_clock(client, p["soc"], incident, regime="eu_nis2")
    base = f"/api/incidents/{incident['id']}/clocks"
    await client.post(
        f"{base}/{gdpr['id']}/milestones/report/met", json={"version": 1}, headers=p["soc"].headers
    )
    await client.post(
        f"{base}/{nis['id']}/close", json={"version": 1, "reason": "Not an OES"}, headers=p["soc"].headers
    )
    # 66 h in: NIS2's early warning is breached and its notification at 90%; the final report is
    # provisional and isn't nudged
    assert await tick(aware + timedelta(hours=66)) == 2
    await settle()
    titles = [
        n["title"] for n in (await client.get("/api/notifications", headers=p["soc"].headers)).json()["items"]
    ]
    assert len([t for t in titles if nis2["ref"] in t]) == 2
    assert not any(gdpr["ref"] in t or nis["ref"] in t for t in titles)


async def test_quiet_hours_hold_back_email_unless_the_incident_is_critical(
    db,
    client,
    people,  # noqa: F811
    outbox,
    settle,
    monkeypatch,  # noqa: F811
):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "mail.example.org")
    monkeypatch.setattr(settings, "smtp_from", "ostraka@example.org")
    p = await people()
    await db.execute(
        update(User)
        .where(User.id == p["soc2"].id)
        .values(quiet_from="00:00", quiet_until="23:59", timezone="UTC")
    )
    await db.commit()
    aware = utcnow() - timedelta(minutes=1)
    for severity in ("high", "critical"):
        incident = await declare(
            client, p["soc"], aware_at=aware.isoformat(), severity=severity, title=f"A {severity} one"
        )
        await client.put(
            f"/api/incidents/{incident['id']}/members/{p['soc2'].id}", json={}, headers=p["soc"].headers
        )
        await add_clock(client, p["soc"], incident, owner_id=str(p["soc2"].id))
    await settle()
    outbox.emails.clear()
    # Half the time gone, inside Sky's quiet hours (all day but the last minute)
    half = aware + timedelta(hours=37)
    if (half.hour, half.minute) == (23, 59):
        half += timedelta(minutes=2)
    assert await tick(half) == 2
    await settle()
    to_sky = [m for m in outbox.emails if m["To"] == "soc2@example.org"]
    assert len(to_sky) == 1 and "A critical one" in to_sky[0].get_content()
    assert await kinds(client, p["soc2"]) == ["clock_due", "clock_due", "clock_due", "clock_due"]


# ---- your work ------------------------------------------------------------------------------------------


async def test_your_work_lists_the_next_deadline_of_clocks_you_own_or_command(db, client, people, outbox):  # noqa: F811
    p = await people()
    aware = utcnow() - timedelta(hours=2)
    incident = await declare(client, p["soc"], aware_at=aware.isoformat())
    await client.put(
        f"/api/incidents/{incident['id']}/members/{p['soc2'].id}", json={}, headers=p["soc"].headers
    )
    nis2 = await add_clock(client, p["soc"], incident, regime="eu_nis2", owner_id=str(p["soc2"].id))
    await add_clock(client, p["soc"], incident)
    base = f"/api/incidents/{incident['id']}/clocks/{nis2['id']}"
    await client.post(f"{base}/milestones/early_warning/met", json={"version": 1}, headers=p["soc2"].headers)

    sky = (await client.get("/api/clocks/mine", headers=p["soc2"].headers)).json()
    assert [(d["clock_ref"], d["key"], d["as_owner"]) for d in sky] == [(nis2["ref"], "notification", True)]
    sam = (await client.get("/api/clocks/mine", headers=p["soc"].headers)).json()
    assert len(sam) == 2 and sam == sorted(sam, key=lambda d: d["due_at"])
    assert {d["as_owner"] for d in sam} == {True, False}
    assert (await client.get("/api/clocks/mine", headers=p["dfir"].headers)).json() == []

    regimes = (await client.get("/api/clocks/regimes", headers=p["exec"].headers)).json()
    dora = next(r for r in regimes if r["key"] == "dora")
    assert dora["milestones"][0]["counted"] == "4 h from classification or 24 h from awareness"
