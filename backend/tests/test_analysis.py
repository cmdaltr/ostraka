"""Assessments, ACH and lessons against Postgres, and who may export an incident's reports."""

import pytest
from sqlalchemy import select

from ostraka.models import AchRating, ActivityEvent, Role
from tests.test_incidents import declare, people  # noqa: F401  (the fixture)

pytestmark = pytest.mark.db

SEARCH = {
    "method": "search",
    "platform": "Defender XDR",
    "query": "DeviceFileEvents | where FileName == 'payroll.zip'",
    "scope": "FIN-WS-07",
}
NO_EGRESS = {
    "absent": "data leaving the network",
    "platform": "Defender XDR",
    "query": "DeviceNetworkEvents | where DeviceName == 'fin-ws-07' and RemoteIPType == 'Public'",
    "scope": "FIN-WS-07",
}


async def finding(client, person, incident_id, title, *, confirm=False) -> dict:
    base = f"/api/incidents/{incident_id}/findings"
    r = await client.post(base, json={"kind": "evidence", "title": title}, headers=person.headers)
    assert r.status_code == 201, r.text
    made = r.json()
    if confirm:
        await client.post(f"{base}/{made['id']}/verifications", json=SEARCH, headers=person.headers)
        r = await client.post(f"{base}/{made['id']}/confirm", json={"version": 1}, headers=person.headers)
        assert r.status_code == 200, r.text
        made = r.json()
    return made


async def test_an_assessment_is_written_from_its_parts_and_linted(db, client, people):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}/assessments"
    staged = await finding(
        client, p["soc"], incident["id"], "Payroll export staged in C:\\Temp", confirm=True
    )
    rclone = await finding(client, p["soc"], incident["id"], "Rclone seen on FIN-WS-07")

    draft = {
        "judgement": "We assess the actor definitely staged the payroll export",
        "probability": "almost_certain",
        "finding_ids": [rclone["id"]],
    }
    r = await client.post(f"{base}/lint", json=draft, headers=p["soc"].headers)
    assert {f["rule"] for f in r.json()["flags"]} == {"unbacked-certainty", "self-framed", "certainty-words"}
    r = await client.post(base, json=draft, headers=p["soc"].headers)
    assert r.status_code == 422 and "confirmed finding" in r.json()["detail"]
    r = await client.post(base, json={**draft, "finding_ids": []}, headers=p["soc"].headers)
    assert r.status_code == 422

    r = await client.post(
        base,
        json={
            "judgement": "the actor staged the payroll export for theft",
            "probability": "likely",
            "finding_ids": [rclone["id"], staged["id"]],
            "absences": [NO_EGRESS],
            "what_next": "Check the proxy logs for the same window.",
        },
        headers=p["soc"].headers,
    )
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["ref"].startswith("ASM-") and first["status"] == "current" and first["flags"] == []
    assert (
        first["sentences"][0]
        == "We assess it is likely (55–75%) that the actor staged the payroll export for theft."
    )
    assert first["sentences"][1].startswith(f"This is based on {staged['ref']} (confirmed")  # strongest first
    assert first["sentences"][2].startswith(
        "There is no evidence of data leaving the network: a Defender XDR"
    )

    r = await client.post(
        base,
        json={
            "judgement": "the actor staged the payroll export for theft",
            "probability": "highly_likely",
            "finding_ids": [staged["id"]],
            "supersedes_id": first["id"],
        },
        headers=p["soc"].headers,
    )
    assert r.status_code == 201 and r.json()["supersedes"] == first["ref"]
    listed = (await client.get(base, headers=p["exec"].headers)).json()  # observers read them
    assert [(a["ref"], a["status"]) for a in listed] == [
        (r.json()["ref"], "current"),
        (first["ref"], "superseded"),
    ]
    again = await client.post(
        base,
        json={**draft, "probability": "likely", "finding_ids": [staged["id"]], "supersedes_id": first["id"]},
        headers=p["soc"].headers,
    )
    assert again.status_code == 409
    assert (await client.post(f"{base}/lint", json=draft, headers=p["exec"].headers)).status_code == 403


async def test_ach_scores_ratings_from_several_analysts(db, client, people):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}/ach"
    staged = await finding(client, p["soc"], incident["id"], "Payroll export staged", confirm=True)

    await client.post(
        f"{base}/hypotheses", json={"statement": "Financially motivated theft"}, headers=p["soc"].headers
    )
    r = await client.post(
        f"{base}/hypotheses",
        json={
            "statement": "An administrator's authorised export",
            "milestone": "HR confirms who asked for it",
        },
        headers=p["soc"].headers,
    )
    theft, admin = r.json()["hypotheses"]
    r = await client.post(
        f"{base}/evidence",
        json={"kind": "finding", "finding_id": staged["id"], "credibility": "h"},
        headers=p["soc"].headers,
    )
    r = await client.post(
        f"{base}/evidence",
        json={"kind": "absence", "text": "No change ticket for the export", "relevance": "h"},
        headers=p["soc"].headers,
    )
    staged_ev, no_ticket = r.json()["evidence"]
    assert (
        staged_ev["text"] == f"{staged['ref']}: Payroll export staged"
        and staged_ev["finding_ref"] == staged["ref"]
    )

    async def rate(person, h, e, value):
        body = {"hypothesis_id": h["id"], "evidence_id": e["id"], "value": value}
        r = await client.put(f"{base}/ratings", json=body, headers=person.headers)
        assert r.status_code == 200, r.text
        return r.json()

    for person in (p["soc"], p["lead"]):
        await rate(person, theft, staged_ev, "c")
        await rate(person, admin, staged_ev, "c")
        await rate(person, theft, no_ticket, "c")
    await rate(p["soc"], admin, no_ticket, "i")
    out = await rate(p["lead"], admin, no_ticket, "c")

    scores = {h["ref"]: h["score"] for h in out["hypotheses"]}
    assert scores == {theft["ref"]: 0.0, admin["ref"]: -0.707}  # 1 × 1.414 ÷ 2 analysts
    assert out["leading"] == theft["ref"] and len(out["raters"]) == 2
    evidence = {e["ref"]: e for e in out["evidence"]}
    assert evidence[staged_ev["ref"]]["non_diagnostic"]  # consistent with both
    disputed = [c for c in out["cells"] if c["disputed"]]
    assert [(c["hypothesis_id"], c["evidence_id"]) for c in disputed] == [(admin["id"], no_ticket["id"])]
    assert "1.414" in out["algorithm"]

    r = await client.post(
        f"{base}/hypotheses/{admin['id']}/reject", json={"version": 1, "reason": ""}, headers=p["soc"].headers
    )
    assert r.status_code == 422
    r = await client.post(
        f"{base}/hypotheses/{admin['id']}/reject",
        json={"version": 1, "reason": "No ticket, and HR asked nobody for it."},
        headers=p["soc"].headers,
    )
    assert r.json()["hypotheses"][1]["status"] == "rejected"
    event = await db.scalar(select(ActivityEvent).where(ActivityEvent.action == "hypothesis_rejected"))
    assert event.data["score"] == -0.707 and "1.414" in event.data["algorithm"]
    r = await client.put(
        f"{base}/ratings",
        json={"hypothesis_id": admin["id"], "evidence_id": no_ticket["id"], "value": "c"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 409

    r = await client.post(
        f"{base}/hypotheses/{admin['id']}/milestone-task",
        json={"assignee_id": str(p["soc"].user.id)},
        headers=p["soc"].headers,
    )
    task = r.json()["hypotheses"][1]["milestone_task"]
    assert task["ref"].startswith("TSK-") and task["status"] == "open"


async def test_at_most_eight_analysts_rate_one_matrix(db, client, people, make_person):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}/ach"
    r = await client.post(f"{base}/hypotheses", json={"statement": "Insider"}, headers=p["soc"].headers)
    hypothesis = r.json()["hypotheses"][0]
    r = await client.post(
        f"{base}/evidence", json={"kind": "assumption", "text": "Logs are complete"}, headers=p["soc"].headers
    )
    evidence = r.json()["evidence"][0]
    for n in range(8):
        other = await make_person(f"analyst{n}@example.org", Role.RESPONDER, "SOC")
        db.add(
            AchRating(
                hypothesis_id=hypothesis["id"], evidence_id=evidence["id"], rater_id=other.user.id, value="c"
            )
        )
    await db.commit()
    body = {"hypothesis_id": hypothesis["id"], "evidence_id": evidence["id"], "value": "i"}
    r = await client.put(f"{base}/ratings", json=body, headers=p["soc"].headers)
    assert r.status_code == 409 and "8 analysts" in r.json()["detail"]


async def test_lessons_record_what_to_change_and_become_tasks(db, client, people):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}/lessons"
    r = await client.post(
        base,
        json={
            "kind": "improve",
            "area": "detection",
            "observation": "Rclone ran for two hours before anyone noticed.",
            "recommendation": "Alert on rclone.exe and renamed copies by hash.",
        },
        headers=p["soc"].headers,
    )
    assert r.status_code == 201, r.text
    lesson = r.json()
    assert lesson["ref"].startswith("LSN-") and lesson["task"] is None
    r = await client.post(
        f"{base}/{lesson['id']}/task", json={"team_id": incident["teams"][0]["id"]}, headers=p["soc"].headers
    )
    assert r.json()["task"]["ref"].startswith("TSK-")
    tasks = (await client.get(f"/api/incidents/{incident['id']}/tasks", headers=p["soc"].headers)).json()
    assert [t["title"] for t in tasks] == ["Alert on rclone.exe and renamed copies by hash."]
    again = await client.post(
        f"{base}/{lesson['id']}/task", json={"team_id": incident["teams"][0]["id"]}, headers=p["soc"].headers
    )
    assert again.status_code == 409
    assert (
        await client.post(
            base,
            json={"kind": "went_well", "area": "people", "observation": "Quick"},
            headers=p["exec"].headers,
        )
    ).status_code == 403


async def test_only_people_who_worked_an_incident_export_and_sensitive_means_the_commander(
    db,
    client,
    people,  # noqa: F811
):
    p = await people()
    incident = await declare(client, p["soc"])  # Sam declares, so commands
    url = f"/api/incidents/{incident['id']}"

    async def can_export(person) -> bool:
        r = await client.get(url, headers=person.headers)
        return r.json()["can_export"]

    assert await can_export(p["soc"])
    assert not await can_export(p["lead"])  # sees it through SOC, hasn't touched it
    assert not await can_export(p["admin"])
    assert not await can_export(p["exec"])
    await client.post(
        f"{url}/lessons",
        json={"kind": "went_well", "area": "people", "observation": "Calm handover"},
        headers=p["lead"].headers,
    )
    assert await can_export(p["lead"])  # recorded something: worked on it

    r = await client.patch(
        url, json={"version": incident["version"], "sensitive": True}, headers=p["soc"].headers
    )
    assert r.status_code == 200 and r.json()["sensitive"]
    assert not await can_export(p["lead"])
    assert await can_export(p["soc"])
    version = r.json()["version"]
    r = await client.patch(url, json={"version": version, "sensitive": False}, headers=p["dfir"].headers)
    assert r.status_code == 404  # DFIR can't see a SOC incident at all
