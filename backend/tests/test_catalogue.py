"""The catalogue of closed records, searched on the server, and the lessons register across records."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from ostraka.models import Incident, utcnow
from tests.test_archive import closed, tick
from tests.test_incidents import declare, people, team_id  # noqa: F401  (people is a fixture)

pytestmark = pytest.mark.db


async def search(client, person, **params) -> dict:
    r = await client.get("/api/catalogue", params=params, headers=person.headers)
    assert r.status_code == 200, r.text
    return r.json()


def refs(page: dict) -> list[str]:
    return [i["ref"] for i in page["items"]]


async def test_the_catalogue_finds_closed_records_by_reference_name_title_and_tag(
    db,
    client,
    people,  # noqa: F811
):
    p = await people()
    phish = await closed(client, db, p["soc"], 5, title="Invoice phishing wave", tags=["phishing", "finance"])
    rule = await closed(client, db, p["soc"], 3, title="Mailbox rule forwarding", tags=["bec"])
    hunt = await closed(client, db, p["soc"], 1, title="Hunting for rclone", kind="hunt")
    await declare(client, p["soc"], title="Invoice phishing, still open")

    everything = await search(client, p["soc"])
    assert refs(everything) == [hunt["ref"], rule["ref"], phish["ref"]]  # most recently closed first
    assert everything["total"] == 3

    assert refs(await search(client, p["soc"], q="invoice")) == [phish["ref"]]
    assert refs(await search(client, p["soc"], q=rule["ref"].lower())) == [rule["ref"]]
    assert refs(await search(client, p["soc"], q=rule["codename"].split()[0])) == [rule["ref"]]
    # the old four-digit year finds it too
    year = phish["ref"].split("-")[1]
    assert refs(await search(client, p["soc"], q=phish["ref"].replace(f"-{year}-", f"-20{year}-"))) == [
        phish["ref"]
    ]
    assert refs(await search(client, p["soc"], tag="phishing")) == [phish["ref"]]
    assert refs(await search(client, p["soc"], tag=["phishing", "bec"])) == []  # every tag, not any
    assert refs(await search(client, p["soc"], kind="hunt")) == [hunt["ref"]]
    assert refs(await search(client, p["soc"], q="100%")) == []  # % is literal


async def test_the_catalogue_finds_by_technique_on_findings_that_werent_refuted(
    db,
    client,
    people,  # noqa: F811
):
    p = await people()
    seen = await declare(client, p["soc"], title="Spearphish with a macro")
    refuted = await declare(client, p["soc"], title="Thought it was PowerShell")
    for record, technique in ((seen, "T1566.001"), (refuted, "T1059.001")):
        r = await client.post(
            f"/api/incidents/{record['id']}/findings",
            json={"kind": "evidence", "title": "What we saw", "techniques": [technique]},
            headers=p["soc"].headers,
        )
        assert r.status_code == 201, r.text
        if record is refuted:
            done = await client.post(
                f"/api/incidents/{record['id']}/findings/{r.json()['id']}/refute",
                json={"version": 1, "note": "It was the backup agent"},
                headers=p["soc"].headers,
            )
            assert done.status_code == 200, done.text
    for record in (seen, refuted):
        r = await client.patch(
            f"/api/incidents/{record['id']}",
            json={"version": 1, "status": "closed"},
            headers=p["soc"].headers,
        )
        assert r.status_code == 200, r.text

    found = await search(client, p["soc"], technique="t1566")
    assert refs(found) == [seen["ref"]] and found["items"][0]["techniques"] == ["T1566.001"]
    assert refs(await search(client, p["soc"], technique="T1566.001")) == [seen["ref"]]
    assert refs(await search(client, p["soc"], technique="T1566.002")) == []
    assert refs(await search(client, p["soc"], technique="T1059")) == []  # refuted
    for wrong in ("T156", "x' OR 1=1"):  # whole technique IDs only
        r = await client.get("/api/catalogue", params={"technique": wrong}, headers=p["soc"].headers)
        assert r.status_code == 422, wrong


async def test_the_catalogue_filters_by_dates_and_state_and_pages(db, client, people):  # noqa: F811
    p = await people()
    made = [
        await closed(client, db, p["soc"], days, title=f"Record closed {days} days ago")
        for days in (2, 40, 120)
    ]
    await db.execute(
        update(Incident)
        .where(Incident.id == made[2]["id"])
        .values(declared_at=datetime(2026, 1, 10, tzinfo=UTC))
    )
    await db.commit()
    await tick(utcnow())

    assert refs(await search(client, p["soc"], state="archived")) == [made[1]["ref"]]
    assert refs(await search(client, p["soc"], state=["archived", "cold"])) == [
        made[1]["ref"],
        made[2]["ref"],
    ]
    cold = (await search(client, p["soc"], state="cold"))["items"][0]
    assert cold["archive_state"] == "cold" and cold["cold_at"]

    today = utcnow().date()
    recent = await search(client, p["soc"], closed_from=str(today - timedelta(days=7)), closed_to=str(today))
    assert refs(recent) == [made[0]["ref"]]
    assert refs(await search(client, p["soc"], opened_to="2026-01-31")) == [made[2]["ref"]]
    assert refs(await search(client, p["soc"], opened_from="2026-01-10", opened_to="2026-01-10")) == [
        made[2]["ref"]
    ]

    first = await search(client, p["soc"], per_page=2)
    second = await search(client, p["soc"], per_page=2, page=2)
    assert first["total"] == second["total"] == 3
    assert refs(first) + refs(second) == [m["ref"] for m in made]
    assert refs(await search(client, p["soc"], per_page=2, page=3)) == []


async def test_the_catalogue_follows_each_records_access(db, client, people):  # noqa: F811
    p = await people()
    soc_only = await closed(client, db, p["soc"], 3, title="SOC's own")
    forensics = await closed(client, db, p["dfir"], 2, title="Forensics' own")

    assert refs(await search(client, p["dfir"])) == [forensics["ref"]]
    assert (await search(client, p["dfir"], q=soc_only["ref"]))["total"] == 0  # not counted either
    assert refs(await search(client, p["exec"])) == [soc_only["ref"]]  # observers see their team's
    assert refs(await search(client, p["admin"])) == [forensics["ref"], soc_only["ref"]]


async def lesson(client, person, incident, **body) -> dict:
    r = await client.post(
        f"/api/incidents/{incident['id']}/lessons",
        json={"kind": "improve", "area": "detection", **body},
        headers=person.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_the_lessons_register_spans_records_and_follows_access(db, client, people):  # noqa: F811
    p = await people()
    first = await closed(client, db, p["soc"], 3, title="Phishing")
    second = await declare(client, p["soc"], title="Still open")
    theirs = await declare(client, p["dfir"], title="Forensics' own")
    alert = await lesson(client, p["soc"], first, observation="No alert when a forwarding rule was made")
    calm = await lesson(
        client, p["soc"], second, kind="went_well", area="people", observation="Calm handover"
    )
    await lesson(client, p["dfir"], theirs, observation="Imaging took too long")
    r = await client.post(
        f"/api/incidents/{second['id']}/lessons/{calm['id']}/task",
        json={"team_id": await team_id(db, "SOC")},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text

    def register(person, **params):
        return client.get("/api/catalogue/lessons", params=params, headers=person.headers)

    everything = (await register(p["soc"])).json()
    assert everything["total"] == 2
    assert [i["ref"] for i in everything["items"]] == [calm["ref"], alert["ref"]]  # newest first
    assert everything["items"][1]["record"]["ref"] == first["ref"]

    assert [i["ref"] for i in (await register(p["soc"], q="forwarding")).json()["items"]] == [alert["ref"]]
    assert [i["ref"] for i in (await register(p["soc"], area="people")).json()["items"]] == [calm["ref"]]
    assert [i["ref"] for i in (await register(p["soc"], kind="improve")).json()["items"]] == [alert["ref"]]
    assert [i["ref"] for i in (await register(p["soc"], follow_up="open")).json()["items"]] == [calm["ref"]]
    assert [i["ref"] for i in (await register(p["soc"], follow_up="none")).json()["items"]] == [alert["ref"]]
    assert (await register(p["soc"], follow_up="done")).json()["total"] == 0
    assert (await register(p["soc"], q="Imaging")).json()["total"] == 0  # Forensics' record
    assert (await register(p["admin"])).json()["total"] == 3
    paged = (await register(p["admin"], per_page=1, page=2)).json()
    assert paged["total"] == 3 and len(paged["items"]) == 1

    # cold store keeps its lessons in the register
    await db.execute(
        update(Incident).where(Incident.id == first["id"]).values(closed_at=utcnow() - timedelta(days=100))
    )
    await db.commit()
    await tick(utcnow())
    kept = (await register(p["soc"], q="forwarding")).json()["items"]
    assert kept[0]["record"]["archive_state"] == "cold"


async def test_an_observer_cant_find_a_lesson_by_an_indicator_they_see_masked(
    db,
    client,
    people,  # noqa: F811
):
    p = await people()
    incident = await declare(client, p["soc"])
    await lesson(client, p["soc"], incident, observation="Beacons to 203.0.113.77 went unseen for a week")

    def register(person, q):
        return client.get("/api/catalogue/lessons", params={"q": q}, headers=person.headers)

    assert (await register(p["soc"], "203.0.113.77")).json()["total"] == 1
    masked = (await register(p["exec"], "203.0.113.77")).json()
    assert masked["total"] == 0 and masked["items"] == []
    seen = (await register(p["exec"], "unseen")).json()
    assert seen["total"] == 1 and "203.0.113.77" not in seen["items"][0]["observation"]


async def test_an_observers_masked_search_pages_past_any_batch_with_no_total(
    db,
    client,
    people,  # noqa: F811
    monkeypatch,
):
    from ostraka.api import catalogue

    monkeypatch.setattr(catalogue, "MASKED_BATCH", 3)  # small batches, so paging crosses several
    p = await people()
    incident = await declare(client, p["soc"])
    for n in range(7):
        await lesson(client, p["soc"], incident, observation=f"Case {n}: beacons went unseen for a week")
        await lesson(client, p["soc"], incident, observation=f"Case {n}: 203.0.113.{n} beaconed out")

    def register(person, **params):
        return client.get(
            "/api/catalogue/lessons", params={"q": "unseen", "per_page": 3, **params}, headers=person.headers
        )

    first = (await register(p["exec"])).json()
    assert first["total"] is None and first["has_more"] and len(first["items"]) == 3
    seen = [i["ref"] for i in first["items"]]
    page = 1
    while first["has_more"]:
        page += 1
        first = (await register(p["exec"], page=page)).json()
        seen += [i["ref"] for i in first["items"]]
    assert len(seen) == len(set(seen)) == 7 and page == 3
    assert first["total"] == 7  # the last page has read everything, so it's counted
    # the responder's search is counted in SQL from the start
    counted = (await register(p["soc"])).json()
    assert counted["total"] == 7 and counted["has_more"]
