"""Phase 3a against Postgres: hunts beside incidents, links between records, promotion, subtasks,
tasks waiting on tasks, playbooks for one kind of record, and the seed playbooks."""

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

from ostraka.models import Playbook, Role
from tests.test_incidents import declare, team_id
from tests.test_work import add_task, notes

pytestmark = pytest.mark.db


@pytest.fixture
def people(make_person, sign_in):
    """SOC (a lead, two responders, an observer), a DFIR responder, and an admin."""

    async def build():
        cast = {
            "admin": await make_person("admin@example.org", Role.ADMIN, None, name="Ada Admin"),
            "lead": await make_person("lead@example.org", Role.TEAM_LEAD, "SOC", name="Lee Lead"),
            "soc": await make_person("soc@example.org", Role.RESPONDER, "SOC", name="Sam Soc"),
            "soc2": await make_person("soc2@example.org", Role.RESPONDER, "SOC", name="Sky Soc"),
            "exec": await make_person("exec@example.org", Role.OBSERVER, "SOC", name="Eve Exec"),
            "dfir": await make_person("dfir@example.org", Role.RESPONDER, "Forensics", name="Dee Dfir"),
        }
        for person in cast.values():
            await sign_in(person)
        return cast

    return build


async def hunt(client, person, **overrides) -> dict:
    overrides.setdefault("title", "Scheduled tasks pointing at user-writable paths")
    return await declare(client, person, kind="hunt", **overrides)


async def link(client, person, record, ref, relation, expect=201) -> dict:
    r = await client.post(
        f"/api/incidents/{record['id']}/links",
        json={"ref": ref, "relation": relation},
        headers=person.headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


async def links(client, person, record) -> dict:
    r = await client.get(f"/api/incidents/{record['id']}/links", headers=person.headers)
    assert r.status_code == 200, r.text
    return r.json()


async def summaries(client, person, record) -> list[str]:
    r = await client.get(f"/api/incidents/{record['id']}/activity", headers=person.headers)
    return [e["summary"] for e in r.json()]


# ---- hunts ----------------------------------------------------------------------------------------


async def test_hunts_number_on_their_own_and_start_with_a_hypothesis(client, people):
    p = await people()
    first_incident = await declare(client, p["soc"])
    first_hunt = await hunt(client, p["soc"])
    second_hunt = await hunt(client, p["soc"], title="Rare parents of rundll32")

    assert first_incident["ref"].startswith("OST-") and first_incident["ref"].endswith("-001")
    assert first_hunt["ref"].startswith("HNT-") and first_hunt["ref"].endswith("-001")
    assert second_hunt["ref"].endswith("-002")
    assert first_hunt["kind"] == "hunt" and first_hunt["phase"] == "hypothesis"

    listed = await client.get("/api/incidents?kind=hunt", headers=p["soc"].headers)
    assert [i["ref"] for i in listed.json()] == [second_hunt["ref"], first_hunt["ref"]]
    resolved = await client.get(f"/api/refs/{first_hunt['ref']}", headers=p["soc"].headers)
    assert resolved.status_code == 200 and resolved.json()["incident_id"] == first_hunt["id"]


async def test_a_hunt_moves_through_its_own_phases_only(client, people):
    p = await people()
    h = await hunt(client, p["soc"])
    url = f"/api/incidents/{h['id']}"
    wrong = await client.patch(url, json={"version": 1, "phase": "contain"}, headers=p["soc"].headers)
    assert wrong.status_code == 422 and "hypothesis, collect, analyse, conclude" in wrong.json()["detail"]
    right = await client.patch(url, json={"version": 1, "phase": "collect"}, headers=p["soc"].headers)
    assert right.status_code == 200 and right.json()["phase"] == "collect"

    incident = await declare(client, p["soc"])
    r = await client.patch(
        f"/api/incidents/{incident['id']}", json={"version": 1, "phase": "analyse"}, headers=p["soc"].headers
    )
    assert r.status_code == 422


async def test_promoting_a_hunt_declares_a_linked_incident_with_its_teams_and_tags(client, people):
    p = await people()
    h = await hunt(client, p["soc"], tags=["persistence"], tlp="green")
    r = await client.post(
        f"/api/incidents/{h['id']}/promote",
        json={"title": "Persistence on FIN-WS-0142", "severity": "high"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 201, r.text
    incident = r.json()
    assert incident["kind"] == "incident" and incident["ref"].startswith("OST-")
    assert incident["tags"] == ["persistence"] and incident["tlp"] == "green"
    assert h["ref"] in incident["summary"]

    assert (await links(client, p["soc"], h))["promoted_to"][0]["ref"] == incident["ref"]
    assert (await links(client, p["soc"], incident))["promoted_from"]["ref"] == h["ref"]
    assert f"Sam Soc promoted {h['ref']} to {incident['ref']}" in await summaries(client, p["soc"], h)

    again = await client.post(
        f"/api/incidents/{incident['id']}/promote", json={"title": "Not a hunt"}, headers=p["soc"].headers
    )
    assert again.status_code == 422


# ---- links ----------------------------------------------------------------------------------------


async def test_parents_and_children_with_one_parent_each_and_no_cycles(client, people):
    p = await people()
    parent = await declare(client, p["soc"], title="Ransomware across the finance estate")
    child = await declare(client, p["soc"], title="Domain admin account used out of hours")
    grandchild = await declare(client, p["soc"], title="VPN session from an unknown ASN")
    other = await declare(client, p["soc"], title="Some other parent")

    shown = await link(client, p["soc"], child, parent["ref"], "child_of")
    assert shown["parent"]["ref"] == parent["ref"]
    await link(client, p["soc"], child, grandchild["ref"], "parent_of")

    assert [c["ref"] for c in (await links(client, p["soc"], parent))["children"]] == [child["ref"]]
    one_parent = await link(client, p["soc"], child, other["ref"], "child_of", expect=409)
    assert one_parent["detail"] == f"{child['ref']} already has a parent."
    cycle = await link(client, p["soc"], parent, grandchild["ref"], "child_of", expect=409)
    assert "already above" in cycle["detail"]
    itself = await link(client, p["soc"], parent, parent["ref"], "related_to", expect=422)
    assert itself["detail"] == "A record can't link to itself."

    for record in (parent, child):
        assert f"Sam Soc made {parent['ref']} the parent of {child['ref']}" in await summaries(
            client, p["soc"], record
        )


async def test_related_and_duplicate_links_and_removing_them(client, people):
    p = await people()
    first = await declare(client, p["soc"])
    second = await declare(client, p["soc"], title="Same mailbox rule, reported twice")
    h = await hunt(client, p["soc"])

    related = await link(client, p["soc"], h, first["ref"], "related_to")
    assert related["related"][0]["ref"] == first["ref"] and related["related"][0]["kind"] == "incident"
    assert (await link(client, p["soc"], first, h["ref"], "related_to", expect=409))["detail"].endswith(
        "are already linked."
    )

    dup = await link(client, p["soc"], second, first["ref"], "duplicate_of")
    assert dup["duplicate_of"]["ref"] == first["ref"]
    closed = (await client.get(f"/api/incidents/{second['id']}", headers=p["soc"].headers)).json()
    assert closed["status"] == "closed"
    assert (await links(client, p["soc"], first))["duplicates"][0]["ref"] == second["ref"]
    onto_closed = await link(client, p["soc"], h, second["ref"], "duplicate_of", expect=409)
    assert onto_closed["detail"] == f"{second['ref']} is closed; reopen it first."

    link_id = related["related"][0]["link_id"]
    r = await client.delete(f"/api/incidents/{h['id']}/links/{link_id}", headers=p["soc"].headers)
    assert r.status_code == 204
    assert (await links(client, p["soc"], h))["related"] == []
    assert f"Sam Soc unlinked {first['ref']} from {h['ref']}" in await summaries(client, p["soc"], first)


async def test_a_record_you_cannot_open_is_counted_not_named(client, people):
    p = await people()
    soc_record = await declare(client, p["soc"])
    dfir_record = await declare(client, p["dfir"], title="DFIR-only collection")
    nobody = await link(client, p["soc"], soc_record, dfir_record["ref"], "related_to", expect=404)
    assert nobody["detail"].endswith("that you can see.")

    await link(client, p["admin"], soc_record, dfir_record["ref"], "related_to")
    seen = await links(client, p["soc"], soc_record)
    assert seen["related"] == [] and seen["hidden"] == 1


async def test_observers_cannot_change_links(client, people):
    p = await people()
    first = await declare(client, p["soc"])
    second = await declare(client, p["soc"], title="Another")
    r = await client.post(
        f"/api/incidents/{first['id']}/links",
        json={"ref": second["ref"], "relation": "related_to"},
        headers=p["exec"].headers,
    )
    assert r.status_code == 403


# ---- subtasks and waiting -------------------------------------------------------------------------


async def test_subtasks_go_one_level_deep(client, people):
    p = await people()
    incident = await declare(client, p["soc"])
    me = str(p["soc"].user.id)
    parent = await add_task(client, p["soc"], incident, title="Contain the finance hosts", assignee_id=me)
    sub = await add_task(
        client, p["soc"], incident, title="Isolate FIN-WS-0142", parent_id=parent["id"], assignee_id=me
    )
    assert sub["parent_id"] == parent["id"]
    r = await client.post(
        f"/api/incidents/{incident['id']}/tasks",
        json={"title": "Too deep", "parent_id": sub["id"], "assignee_id": me},
        headers=p["soc"].headers,
    )
    assert r.status_code == 422 and r.json()["detail"] == "Subtasks go one level deep."


async def test_a_task_waits_on_others_and_its_owner_hears_when_the_last_one_finishes(client, people, settle):
    p = await people()
    incident = await declare(client, p["soc"])
    elsewhere = await declare(client, p["soc"], title="The ransomware incident")
    me = str(p["soc2"].user.id)
    waiting = await add_task(client, p["soc"], incident, title="Reset the krbtgt twice", assignee_id=me)
    mine = str(p["soc"].user.id)
    first = await add_task(client, p["soc"], incident, title="Confirm every DC is clean", assignee_id=mine)
    second = await add_task(
        client, p["soc"], elsewhere, title="Rebuild DC03 from known good", assignee_id=mine
    )

    base = f"/api/incidents/{incident['id']}/tasks/{waiting['id']}/blocked-by"
    r = await client.post(base, json={"ref": first["ref"]}, headers=p["soc"].headers)
    assert r.status_code == 201 and [b["ref"] for b in r.json()["blocked_by"]] == [first["ref"]]
    r = await client.post(base, json={"ref": second["ref"]}, headers=p["soc"].headers)
    assert r.json()["blocked_by"][1]["incident_ref"] == elsewhere["ref"]

    itself = await client.post(base, json={"ref": waiting["ref"]}, headers=p["soc"].headers)
    assert itself.status_code == 422
    loop = await client.post(
        f"/api/incidents/{incident['id']}/tasks/{first['id']}/blocked-by",
        json={"ref": waiting["ref"]},
        headers=p["soc"].headers,
    )
    assert (
        loop.status_code == 409
        and loop.json()["detail"] == f"{waiting['ref']} already waits on {first['ref']}."
    )

    async def finish(record, task):
        r = await client.patch(
            f"/api/incidents/{record['id']}/tasks/{task['id']}",
            json={"version": task["version"], "status": "done"},
            headers=p["soc"].headers,
        )
        assert r.status_code == 200, r.text

    await finish(incident, first)
    await settle()
    assert not [n for n in (await notes(client, p["soc2"]))["items"] if n["kind"] == "unblocked"]

    await finish(elsewhere, second)
    await settle()
    unblocked = [n for n in (await notes(client, p["soc2"]))["items"] if n["kind"] == "unblocked"]
    assert [n["title"] for n in unblocked] == [f"{waiting['ref']} can go ahead: {second['ref']} is finished"]

    r = await client.delete(f"{base}/{first['id']}", headers=p["soc"].headers)
    assert r.status_code == 200 and [b["ref"] for b in r.json()["blocked_by"]] == [second["ref"]]


# ---- playbooks ------------------------------------------------------------------------------------


async def test_a_playbook_applies_only_to_its_kind_of_record(client, people, db):
    p = await people()
    made = await client.post(
        "/api/playbooks",
        json={
            "name": "Threat hunt",
            "applies_to": "hunt",
            "steps": [{"title": "Write the hypothesis", "team_id": await team_id(db, "SOC")}],
        },
        headers=p["lead"].headers,
    )
    assert made.status_code == 201, made.text
    incident = await declare(client, p["soc"])
    r = await client.post(
        f"/api/incidents/{incident['id']}/playbooks/{made.json()['id']}", headers=p["soc"].headers
    )
    assert r.status_code == 409 and r.json()["detail"] == "Threat hunt is for hunts, not incidents."
    h = await hunt(client, p["soc"])
    r = await client.post(f"/api/incidents/{h['id']}/playbooks/{made.json()['id']}", headers=p["soc"].headers)
    assert r.status_code == 201 and r.json()[0]["title"] == "Write the hypothesis"


async def test_the_seed_playbooks(db):
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0005_hunts_and_links.py"
    spec = importlib.util.spec_from_file_location("seed_revision", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    def seed(connection):
        with Operations.context(MigrationContext.configure(connection)):
            revision._seed()

    await (await db.connection()).run_sync(seed)
    await db.commit()
    seeded = {pb.name: pb for pb in (await db.scalars(select(Playbook))).all()}
    assert set(seeded) == {
        "Incident response",
        "Phishing and BEC",
        "Ransomware",
        "Account compromise",
        "Data exposure",
        "Threat hunt",
    }
    assert seeded["Threat hunt"].applies_to.value == "hunt"
    assert all(pb.applies_to.value == "incident" for name, pb in seeded.items() if name != "Threat hunt")
