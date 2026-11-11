"""Playbooks as workflows against Postgres: steps open when what they follow finishes or the record
reaches their phase, each opening tells the next team, and a decision's outcome acts on the record
and decides what opens next, including going round again."""

import pytest
from sqlalchemy import select

from ostraka.models import Playbook, Role
from tests.test_incidents import declare, team_id
from tests.test_work import notes

pytestmark = pytest.mark.db


@pytest.fixture
def people(make_person, sign_in):
    async def build():
        cast = {
            "admin": await make_person("admin@example.org", Role.ADMIN, None, name="Ada Admin"),
            "lead": await make_person("lead@example.org", Role.TEAM_LEAD, "SOC", name="Lee Lead"),
            "soc": await make_person("soc@example.org", Role.RESPONDER, "SOC", name="Sam Soc"),
            "for": await make_person("for@example.org", Role.RESPONDER, "Forensics", name="Fran Forensics"),
            "cti": await make_person("cti@example.org", Role.RESPONDER, "Intelligence", name="Ivy Intel"),
            "cth": await make_person("cth@example.org", Role.TEAM_LEAD, "Threat Hunting", name="Hal Hunter"),
            "im": await make_person(
                "im@example.org", Role.RESPONDER, "Incident Management", name="Ian Manager"
            ),
        }
        for person in cast.values():
            await sign_in(person)
        return cast

    return build


async def playbook_id(db, name: str) -> str:
    return str(await db.scalar(select(Playbook.id).where(Playbook.name == name)))


async def workflow(client, person, record) -> list[dict]:
    r = await client.get(f"/api/incidents/{record['id']}/playbooks", headers=person.headers)
    assert r.status_code == 200, r.text
    return r.json()[0]["steps"]


async def tasks(client, person, record) -> dict[str, dict]:
    r = await client.get(f"/api/incidents/{record['id']}/tasks", headers=person.headers)
    return {t["title"]: t for t in r.json()}


async def finish(client, person, record, task) -> dict:
    r = await client.patch(
        f"/api/incidents/{record['id']}/tasks/{task['id']}",
        json={"version": task["version"], "status": "done"},
        headers=person.headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def claim(client, person, record, task) -> dict:
    r = await client.post(f"/api/incidents/{record['id']}/tasks/{task['id']}/claim", headers=person.headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_steps_open_as_the_steps_before_them_finish_and_each_team_hears(
    db, seed_playbooks, client, people, settle
):
    p = await people()
    incident = await declare(
        client, p["soc"], title="Ransomware on FS01", playbook_ids=[await playbook_id(db, "Ransomware")]
    )
    open_now = await tasks(client, p["soc"], incident)
    # Only what nothing waits for: isolate, check backups, start the clocks
    assert set(open_now) == {
        "Isolate affected hosts",
        "Check backups are intact and offline",
        "Start the regulator and insurer clocks",
    }
    steps = await workflow(client, p["soc"], incident)
    assert [s["state"] for s in steps[:3]] == ["open", "waiting", "waiting"]

    isolate = await claim(client, p["soc"], incident, open_now["Isolate affected hosts"])
    await finish(client, p["soc"], incident, isolate)
    await settle()
    preserve = (await tasks(client, p["soc"], incident))["Preserve memory and disk"]
    assert preserve["team"]["name"] == "Forensics"
    heard = [n["title"] for n in (await notes(client, p["for"]))["items"]]
    assert f"Ready for Forensics: {preserve['ref']} Preserve memory and disk" in heard

    # Forensics finishes its chain; Intelligence isn't involved yet, and a responder can't involve a
    # team, but the playbook brings it in for its step
    preserve = await claim(client, p["for"], incident, preserve)
    await finish(client, p["for"], incident, preserve)
    zero = await claim(
        client,
        p["for"],
        incident,
        (await tasks(client, p["for"], incident))["Identify patient zero and the strain"],
    )
    await finish(client, p["for"], incident, zero)
    now = await tasks(client, p["for"], incident)
    assert now["Profile the group and watch its leak site"]["team"]["name"] == "Intelligence"
    assert now["Scope encryption and exfiltration"]["status"] == "open"
    detail = (await client.get(f"/api/incidents/{incident['id']}", headers=p["cti"].headers)).json()
    assert "Intelligence" in [t["name"] for t in detail["teams"]]
    # The recovery plan waits for both the backups check and the scoping
    assert "Agree the recovery plan with Leadership" not in now


async def test_a_step_with_a_phase_opens_when_the_record_gets_there(db, seed_playbooks, client, people):
    p = await people()
    hunt = await declare(
        client,
        p["cth"],
        kind="hunt",
        title="Rare rundll32 parents",
        playbook_ids=[await playbook_id(db, "Threat hunt")],
    )
    first = await tasks(client, p["cth"], hunt)
    assert list(first) == ["Write the hypothesis"]
    await finish(client, p["cth"], hunt, first["Write the hypothesis"])
    # Finished, but choosing data sources waits for the Collect phase
    assert "Choose data sources and time range" not in await tasks(client, p["cth"], hunt)
    r = await client.patch(
        f"/api/incidents/{hunt['id']}",
        json={"version": hunt["version"], "phase": "collect"},
        headers=p["cth"].headers,
    )
    assert r.status_code == 200
    assert (await tasks(client, p["cth"], hunt))["Choose data sources and time range"]["status"] == "open"


async def run_hunt_to_conclusion(client, p, db):
    hunt = await declare(
        client,
        p["cth"],
        kind="hunt",
        title="Kerberoasting",
        playbook_ids=[await playbook_id(db, "Threat hunt")],
    )
    t = await tasks(client, p["cth"], hunt)
    await finish(client, p["cth"], hunt, t["Write the hypothesis"])
    current = (await client.get(f"/api/incidents/{hunt['id']}", headers=p["cth"].headers)).json()
    await client.patch(
        f"/api/incidents/{hunt['id']}",
        json={"version": current["version"], "phase": "collect"},
        headers=p["cth"].headers,
    )
    for title in ("Choose data sources and time range", "Run the searches (Shikari)"):
        task = await claim(client, p["cth"], hunt, (await tasks(client, p["cth"], hunt))[title])
        await finish(client, p["cth"], hunt, task)
    current = (await client.get(f"/api/incidents/{hunt['id']}", headers=p["cth"].headers)).json()
    await client.patch(
        f"/api/incidents/{hunt['id']}",
        json={"version": current["version"], "phase": "analyse"},
        headers=p["cth"].headers,
    )
    await finish(client, p["cth"], hunt, (await tasks(client, p["cth"], hunt))["Analyse the results"])
    conclude = (await tasks(client, p["cth"], hunt))["Conclude"]
    return hunt, conclude


async def decide(client, person, record, task, outcome, **extra):
    return await client.post(
        f"/api/incidents/{record['id']}/tasks/{task['id']}/decide",
        json={"version": task["version"], "outcome": outcome, **extra},
        headers=person.headers,
    )


async def test_a_decision_is_finished_by_its_outcome_and_refuted_closes_the_hunt(
    db, seed_playbooks, client, people
):
    p = await people()
    hunt, conclude = await run_hunt_to_conclusion(client, p, db)
    assert conclude["outcomes"] == [
        "Confirmed: promote to an incident",
        "Refuted: close the hunt",
        "Inconclusive: refine the hypothesis",
    ]
    r = await client.patch(
        f"/api/incidents/{hunt['id']}/tasks/{conclude['id']}",
        json={"version": conclude["version"], "status": "done"},
        headers=p["cth"].headers,
    )
    assert r.status_code == 409 and "choose one of its outcomes" in r.json()["detail"]
    assert (await decide(client, p["cth"], hunt, conclude, "Maybe")).status_code == 422

    r = await decide(
        client, p["cth"], hunt, conclude, "Refuted: close the hunt", note="Only the backup server."
    )
    assert r.status_code == 200 and r.json()["outcome"] == "Refuted: close the hunt"
    closed = (await client.get(f"/api/incidents/{hunt['id']}", headers=p["cth"].headers)).json()
    assert closed["status"] == "closed"
    steps = {s["title"]: s for s in await workflow(client, p["cth"], hunt)}
    assert steps["Write up the detection for Shikari's library"]["state"] == "open"
    assert steps["Share what was found with Intelligence"]["state"] == "skipped"
    assert steps["Conclude"]["outcome"] == "Refuted: close the hunt"
    activity = (await client.get(f"/api/incidents/{hunt['id']}/activity", headers=p["cth"].headers)).json()
    assert any(
        e["summary"].startswith(f"Hal Hunter decided {conclude['ref']}: Refuted") and "closed" in e["summary"]
        for e in activity
    )


async def test_confirmed_promotes_and_opens_the_hand_offs(db, seed_playbooks, client, people):
    p = await people()
    hunt, conclude = await run_hunt_to_conclusion(client, p, db)
    missing = await decide(client, p["cth"], hunt, conclude, "Confirmed: promote to an incident")
    assert missing.status_code == 422
    r = await decide(
        client,
        p["cth"],
        hunt,
        conclude,
        "Confirmed: promote to an incident",
        promote={"title": "Service account roasted from FIN-WS-0142", "severity": "high"},
    )
    assert r.status_code == 200, r.text
    links = (await client.get(f"/api/incidents/{hunt['id']}/links", headers=p["cth"].headers)).json()
    assert links["promoted_to"][0]["title"] == "Service account roasted from FIN-WS-0142"
    now = await tasks(client, p["cth"], hunt)
    assert now["Share what was found with Intelligence"]["team"]["name"] == "Intelligence"
    assert now["Write up the detection for Shikari's library"]["status"] == "open"


async def test_inconclusive_goes_round_again(db, seed_playbooks, client, people):
    p = await people()
    hunt, conclude = await run_hunt_to_conclusion(client, p, db)
    r = await decide(client, p["cth"], hunt, conclude, "Inconclusive: refine the hypothesis")
    assert r.status_code == 200
    current = (await client.get(f"/api/incidents/{hunt['id']}", headers=p["cth"].headers)).json()
    assert current["phase"] == "hypothesis" and current["status"] == "open"
    steps = {s["title"]: s for s in await workflow(client, p["cth"], hunt)}
    assert steps["Write the hypothesis"]["state"] == "open" and steps["Write the hypothesis"]["runs"] == 2
    assert steps["Conclude"]["state"] == "waiting" and steps["Conclude"]["outcome"] is None
    assert steps["Write up the detection for Shikari's library"]["state"] == "waiting"
    hypotheses = [
        t
        for t in (await client.get(f"/api/incidents/{hunt['id']}/tasks", headers=p["cth"].headers)).json()
        if t["title"] == "Write the hypothesis"
    ]
    assert sorted(t["status"] for t in hypotheses) == ["done", "open"]


async def test_not_reportable_skips_telling_people(db, seed_playbooks, client, people):
    p = await people()
    leak = await declare(
        client,
        p["soc"],
        title="Folder shared publicly",
        playbook_ids=[await playbook_id(db, "Data exposure")],
    )
    confirm = await claim(
        client, p["for"], leak, (await tasks(client, p["for"], leak))["Confirm what data, and whose"]
    )
    await finish(client, p["for"], leak, confirm)
    assess = await claim(
        client, p["im"], leak, (await tasks(client, p["im"], leak))["Assess the risk to the people affected"]
    )
    await finish(client, p["im"], leak, assess)
    ico = await claim(
        client, p["im"], leak, (await tasks(client, p["im"], leak))["Report to the ICO if required"]
    )
    r = await decide(
        client, p["im"], leak, ico, "Not reportable: record why", note="Only a crawler opened it."
    )
    assert r.status_code == 200
    steps = {s["title"]: s for s in await workflow(client, p["im"], leak)}
    assert steps["Tell the people affected"]["state"] == "skipped"
    assert steps["Tell the people affected"]["conditional"] is True


async def test_playbooks_are_checked_as_workflows(db, client, people):
    p = await people()
    soc = await team_id(db, "SOC")
    base = {"name": "Broken", "applies_to": "hunt"}
    forward = await client.post(
        "/api/playbooks",
        json={
            **base,
            "steps": [{"title": "First", "team_id": soc, "after": [1]}, {"title": "Second", "team_id": soc}],
        },
        headers=p["lead"].headers,
    )
    assert forward.status_code == 422 and "only follow steps before it" in forward.json()["detail"]
    wrong_phase = await client.post(
        "/api/playbooks",
        json={**base, "steps": [{"title": "First", "phase": "contain"}]},
        headers=p["lead"].headers,
    )
    assert wrong_phase.status_code == 422 and "isn't a phase here" in wrong_phase.json()["detail"]
    nowhere = await client.post(
        "/api/playbooks",
        json={**base, "steps": [{"title": "Decide", "outcomes": [{"label": "Go", "opens": [3]}]}]},
        headers=p["lead"].headers,
    )
    assert nowhere.status_code == 422
    good = await client.post(
        "/api/playbooks",
        json={
            **base,
            "steps": [
                {"title": "Look", "phase": "collect"},
                {
                    "title": "Decide",
                    "after": [0],
                    "outcomes": [{"label": "Found it", "action": "close", "opens": [2]}],
                },
                {"title": "Write it up", "team_id": soc},
            ],
        },
        headers=p["lead"].headers,
    )
    assert good.status_code == 201, good.text
    assert good.json()["steps"][1]["outcomes"][0] == {
        "label": "Found it",
        "action": "close",
        "phase": None,
        "opens": [2],
    }
