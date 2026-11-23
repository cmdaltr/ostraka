"""Archiving closed records, cold store, and restoring one with another senior person's approval."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from ostraka import scheduler
from ostraka.db import sessions
from ostraka.models import (
    ActivityEvent,
    ArchiveRequest,
    Incident,
    Notification,
    NotificationKind,
    SecurityEvent,
    utcnow,
)
from tests.test_evidence import EXHIBIT
from tests.test_incidents import declare, people  # noqa: F401  (people is a fixture)

pytestmark = pytest.mark.db


async def tick(now):
    async with sessions()() as session:
        return await scheduler.tick(session, now=now)


async def closed(client, db, person, days_ago: float, **overrides) -> dict:
    """A record closed `days_ago` days ago."""
    incident = await declare(client, person, **overrides)
    r = await client.patch(
        f"/api/incidents/{incident['id']}", json={"version": 1, "status": "closed"}, headers=person.headers
    )
    assert r.status_code == 200, r.text
    await db.execute(
        update(Incident)
        .where(Incident.id == incident["id"])
        .values(closed_at=utcnow() - timedelta(days=days_ago))
    )
    await db.commit()
    return r.json()


async def get(client, person, incident) -> dict:
    r = await client.get(f"/api/incidents/{incident['id']}", headers=person.headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_closed_record_is_archived_then_cold_and_the_log_says_so(db, client, people):  # noqa: F811
    p = await people()
    young = await closed(client, db, p["soc"], 10)
    month = await closed(client, db, p["soc"], 31, title="A month closed")
    old = await closed(client, db, p["soc"], 120, title="Long closed")
    still_open = await declare(client, p["soc"], title="Still open")

    seen = await get(client, p["soc"], young)
    assert seen["archive"]["state"] == "active" and seen["archive"]["next_state"] == "archived"
    assert seen["archive"]["can_set"] and not seen["archive"]["can_restore"]

    await tick(utcnow())
    assert (await get(client, p["soc"], young))["archive_state"] == "active"
    assert (await get(client, p["soc"], still_open))["archive_state"] == "active"
    month_now = await get(client, p["soc"], month)
    assert month_now["archive_state"] == "archived" and month_now["archive"]["next_state"] == "cold"
    assert not month_now["can_edit"] and not month_now["can_manage"]
    assert month_now["archive"]["can_restore"] and not month_now["archive"]["can_set"]
    assert (await get(client, p["soc"], old))["archive_state"] == "cold"

    said = (
        await db.scalars(
            select(ActivityEvent.summary).where(
                ActivityEvent.incident_id == month["id"], ActivityEvent.action == "record_archived"
            )
        )
    ).all()
    assert said == [f"Ostraka archived {month['ref']}: closed 30 days ago"]
    assert await tick(utcnow()) == 0  # once


async def test_an_archived_record_is_read_only_and_a_cold_one_shows_its_entry_and_lessons(
    db,
    client,
    people,  # noqa: F811
):
    p = await people()
    archived = await closed(client, db, p["soc"], 40)
    cold = await closed(client, db, p["soc"], 100, title="In the cold")
    for record in (archived, cold):
        r = await client.post(
            f"/api/incidents/{record['id']}/lessons",
            json={"kind": "improve", "area": "detection", "observation": "No alert on the forwarding rule"},
            headers=p["soc"].headers,
        )
        assert r.status_code == 201, r.text
    await tick(utcnow())

    base = f"/api/incidents/{archived['id']}"
    assert (await client.get(f"{base}/findings", headers=p["soc"].headers)).status_code == 200
    r = await client.post(
        f"{base}/findings", json={"kind": "evidence", "title": "Late"}, headers=p["soc"].headers
    )
    assert r.status_code == 423 and "archived" in r.json()["detail"]
    r = await client.patch(base, json={"version": 3, "status": "open"}, headers=p["admin"].headers)
    assert r.status_code == 423
    r = await client.put(f"{base}/channel", json={"url": ""}, headers=p["soc"].headers)
    assert r.status_code == 423

    cold_base = f"/api/incidents/{cold['id']}"
    seen = await get(client, p["soc"], cold)
    assert seen["archive_state"] == "cold" and seen["title"] == "In the cold"
    lessons = await client.get(f"{cold_base}/lessons", headers=p["soc"].headers)
    assert lessons.status_code == 200 and len(lessons.json()) == 1
    for section in ("findings", "tasks", "observables", "activity", "timeline"):
        r = await client.get(f"{cold_base}/{section}", headers=p["soc"].headers)
        assert r.status_code == 423 and "cold store" in r.json()["detail"], section
    # someone who can't see it still gets nothing
    assert (await client.get(cold_base, headers=p["dfir"].headers)).status_code == 404


async def test_custody_of_an_exhibit_carries_on_after_its_record_is_archived(
    db,
    client,
    people,  # noqa: F811
):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    item = (await client.post(f"{base}/evidence", json=EXHIBIT, headers=p["soc"].headers)).json()
    r = await client.patch(base, json={"version": 1, "status": "closed"}, headers=p["soc"].headers)
    assert r.status_code == 200
    await db.execute(
        update(Incident).where(Incident.id == incident["id"]).values(closed_at=utcnow() - timedelta(days=100))
    )
    await db.commit()
    await tick(utcnow())
    assert (await get(client, p["soc"], incident))["archive_state"] == "cold"

    listed = await client.get(f"{base}/evidence", headers=p["soc"].headers)
    assert listed.status_code == 200 and listed.json()[0]["can_offer"]
    url = f"{base}/evidence/{item['id']}"
    r = await client.post(
        f"{url}/offer",
        json={"to_id": str(p["lead"].id), "purpose": "Return to the client"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["pending"]["to_person"]["name"] == "Lee Lead"
    # but nothing new is recorded as evidence
    r = await client.post(f"{base}/evidence", json=EXHIBIT, headers=p["soc"].headers)
    assert r.status_code == 423


async def test_a_restore_needs_another_senior_person_within_24_hours(
    db,
    client,
    people,  # noqa: F811
    outbox,
    settle,
):
    p = await people()
    incident = await closed(client, db, p["soc"], 45)
    await tick(utcnow())
    base = f"/api/incidents/{incident['id']}/restore"

    reason = {"reason": "The client reported the same sender again"}
    assert (await client.post(base, json=reason, headers=p["exec"].headers)).status_code == 403
    assert (await client.post(base, json=reason, headers=p["dfir"].headers)).status_code == 404
    r = await client.post(base, json=reason, headers=p["lead"].headers)
    assert r.status_code == 201, r.text
    asked = r.json()["archive"]["request"]
    assert asked["outcome"] == "pending" and asked["can_withdraw"] and not asked["can_decide"]
    assert (await client.post(base, json=reason, headers=p["soc"].headers)).status_code == 409
    await settle()

    # the admin hears; the lead who asked doesn't
    heard = (
        await db.scalars(
            select(Notification.user_id).where(Notification.kind == NotificationKind.ARCHIVE_ASKED)
        )
    ).all()
    assert heard == [p["admin"].id]

    decide = f"/api/incidents/{incident['id']}/requests/{asked['id']}"
    for who in ("lead", "soc", "exec"):  # the asker, a responder, an observer
        r = await client.post(f"{decide}/approve", json={}, headers=p[who].headers)
        assert r.status_code == 403, who
    waiting = (await client.get("/api/archive-requests", headers=p["admin"].headers)).json()
    assert [w["record"]["ref"] for w in waiting] == [incident["ref"]] and waiting[0]["request"]["can_decide"]

    r = await client.post(f"{decide}/approve", json={"note": "Go ahead"}, headers=p["admin"].headers)
    assert r.status_code == 200, r.text
    restored = r.json()
    assert restored["archive_state"] == "active" and restored["status"] == "closed"
    assert restored["archive"]["request"] is None and restored["archive"]["restored_at"]
    assert restored["can_manage"]
    # once only
    assert (await client.post(f"{decide}/approve", json={}, headers=p["admin"].headers)).status_code == 409

    # the days start again from the restore, so the next tick leaves it be
    await tick(utcnow())
    assert (await get(client, p["soc"], incident))["archive_state"] == "active"
    await tick(utcnow() + timedelta(days=31))
    assert (await get(client, p["soc"], incident))["archive_state"] == "archived"

    actions = (
        await db.scalars(select(SecurityEvent.action).where(SecurityEvent.action.like("archive_%")))
    ).all()
    assert sorted(actions) == ["archive_request_approved", "archive_requested"]
    said = await db.scalar(
        select(ActivityEvent.summary).where(
            ActivityEvent.incident_id == incident["id"], ActivityEvent.action == "record_restored"
        )
    )
    assert said == f"Ada Admin approved Lee Lead's request and restored {incident['ref']}, which was archived"


async def test_a_request_nobody_approves_lapses_and_one_can_be_declined_or_withdrawn(
    db,
    client,
    people,  # noqa: F811
):
    p = await people()
    incident = await closed(client, db, p["soc"], 45)
    await tick(utcnow())
    base = f"/api/incidents/{incident['id']}/restore"
    requests = f"/api/incidents/{incident['id']}/requests"
    reason = {"reason": "Audit asked for the timeline"}

    first = (await client.post(base, json=reason, headers=p["soc"].headers)).json()["archive"]["request"]
    await db.execute(
        update(ArchiveRequest)
        .where(ArchiveRequest.id == first["id"])
        .values(expires_at=utcnow() - timedelta(minutes=1))
    )
    await db.commit()
    r = await client.post(f"{requests}/{first['id']}/approve", json={}, headers=p["lead"].headers)
    assert r.status_code == 410
    await tick(utcnow())
    assert (
        await db.scalar(select(ArchiveRequest.outcome).where(ArchiveRequest.id == first["id"]))
    ) == "expired"
    seen = await get(client, p["soc"], incident)
    assert seen["archive_state"] == "archived" and seen["archive"]["request"] is None
    told = await db.scalar(
        select(Notification.title).where(
            Notification.user_id == p["soc"].id, Notification.kind == NotificationKind.ARCHIVE_DECIDED
        )
    )
    assert told == f"Your request to restore {incident['ref']} lapsed"

    second = (await client.post(base, json=reason, headers=p["soc"].headers)).json()["archive"]["request"]
    r = await client.post(
        f"{requests}/{second['id']}/decline",
        json={"note": "Export the report instead"},
        headers=p["lead"].headers,
    )
    assert r.status_code == 200 and r.json()["archive_state"] == "archived"

    third = (await client.post(base, json=reason, headers=p["soc"].headers)).json()["archive"]["request"]
    assert (await client.delete(f"{requests}/{third['id']}", headers=p["lead"].headers)).status_code == 403
    r = await client.delete(f"{requests}/{third['id']}", headers=p["soc"].headers)
    assert r.status_code == 200 and r.json()["archive"]["can_restore"]


async def test_each_record_sets_its_own_days_or_stays_out_of_the_archive(
    db,
    client,
    people,  # noqa: F811
):
    p = await people()
    incident = await closed(client, db, p["soc"], 20)
    url = f"/api/incidents/{incident['id']}/archiving"
    body = {"version": incident["version"], "auto": True, "archive_after_days": 14, "cold_after_days": 60}
    assert (await client.put(url, json=body, headers=p["exec"].headers)).status_code == 403
    assert (await client.put(url, json=body, headers=p["dfir"].headers)).status_code == 404
    bad = {**body, "cold_after_days": 10}
    assert (await client.put(url, json=bad, headers=p["soc"].headers)).status_code == 422
    r = await client.put(url, json=body, headers=p["soc"].headers)
    assert r.status_code == 200, r.text
    assert r.json()["archive"]["archive_after_days"] == 14
    assert (await client.put(url, json=body, headers=p["soc"].headers)).status_code == 409  # stale version

    await tick(utcnow())
    assert (await get(client, p["soc"], incident))["archive_state"] == "archived"

    kept = await closed(client, db, p["soc"], 200, title="Kept out")
    r = await client.put(
        f"/api/incidents/{kept['id']}/archiving",
        json={"version": kept["version"], "auto": False, "archive_after_days": 30, "cold_after_days": 90},
        headers=p["lead"].headers,
    )
    assert r.status_code == 200 and r.json()["archive"]["next_state"] is None
    await tick(utcnow())
    assert (await get(client, p["soc"], kept))["archive_state"] == "active"


async def test_archiving_early_is_asked_by_whoever_manages_it_and_approved_by_someone_else(
    db,
    client,
    people,  # noqa: F811
    outbox,
    settle,
):
    p = await people()
    incident = await closed(client, db, p["soc"], 2)
    url = f"/api/incidents/{incident['id']}/archive"
    why = {"reason": "Engagement over; the client asked us to close the file", "to_state": "cold"}

    seen = await get(client, p["soc"], incident)
    assert seen["archive"]["archive_to"] == ["archived", "cold"]
    assert (await get(client, p["exec"], incident))["archive"]["archive_to"] == []
    assert (await client.post(url, json=why, headers=p["exec"].headers)).status_code == 403
    assert (await client.post(url, json=why, headers=p["dfir"].headers)).status_code == 404
    still_open = await declare(client, p["soc"], title="Still open")
    r = await client.post(f"/api/incidents/{still_open['id']}/archive", json=why, headers=p["soc"].headers)
    assert r.status_code == 409 and "Close" in r.json()["detail"]

    r = await client.post(url, json=why, headers=p["soc"].headers)
    assert r.status_code == 201, r.text
    asked = r.json()["archive"]["request"]
    assert asked["action"] == "archive" and asked["to_state"] == "cold" and asked["can_withdraw"]
    assert r.json()["archive_state"] == "active"  # nothing happens until someone approves
    # one request at a time, of either kind
    assert (await client.post(url, json=why, headers=p["lead"].headers)).status_code == 409
    await settle()
    heard = (
        await db.scalars(
            select(Notification.title).where(Notification.kind == NotificationKind.ARCHIVE_ASKED)
        )
    ).all()
    assert f"Sam Soc asks to put {incident['ref']} in cold store" in heard

    decide = f"/api/incidents/{incident['id']}/requests/{asked['id']}"
    assert (await client.post(f"{decide}/approve", json={}, headers=p["soc"].headers)).status_code == 403
    r = await client.post(f"{decide}/approve", json={}, headers=p["lead"].headers)
    assert r.status_code == 200, r.text
    assert r.json()["archive_state"] == "cold" and r.json()["archive"]["archive_to"] == []
    said = await db.scalar(
        select(ActivityEvent.summary).where(
            ActivityEvent.incident_id == incident["id"], ActivityEvent.action == "record_cold_stored"
        )
    )
    assert said.startswith("Lee Lead approved Sam Soc's request and put it in cold store ahead of its days")
    actions = (
        await db.scalars(select(SecurityEvent.action).where(SecurityEvent.action.like("archive_%")))
    ).all()
    assert sorted(actions) == ["archive_request_approved", "archive_requested"]


async def test_an_archive_request_is_withdrawn_when_the_record_reopens_or_moves_on_first(
    db,
    client,
    people,  # noqa: F811
):
    p = await people()
    reopened = await closed(client, db, p["soc"], 2)
    r = await client.post(
        f"/api/incidents/{reopened['id']}/archive",
        json={"reason": "Nothing more to do here"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 201 and r.json()["archive"]["request"]["to_state"] == "archived"
    asked = r.json()["archive"]["request"]
    r = await client.patch(
        f"/api/incidents/{reopened['id']}",
        json={"version": r.json()["version"], "status": "open"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["archive"]["request"] is None
    gone = await db.scalar(select(ArchiveRequest).where(ArchiveRequest.id == asked["id"]))
    await db.refresh(gone)
    assert gone.outcome == "withdrawn" and gone.note == "it was reopened"

    # Ostraka archives it on its days before anyone approves: the request has nothing left to do
    overtaken = await closed(client, db, p["soc"], 29, title="Nearly a month")
    asked = (
        await client.post(
            f"/api/incidents/{overtaken['id']}/archive",
            json={"reason": "Done with it"},
            headers=p["soc"].headers,
        )
    ).json()["archive"]["request"]
    await tick(utcnow() + timedelta(days=2))
    seen = await get(client, p["soc"], overtaken)
    assert seen["archive_state"] == "archived" and seen["archive"]["request"] is None
    r = await client.post(
        f"/api/incidents/{overtaken['id']}/requests/{asked['id']}/approve", json={}, headers=p["lead"].headers
    )
    assert r.status_code == 409
