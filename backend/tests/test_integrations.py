"""The suite links against Postgres, with Rowanholt and Shikari stubbed through httpx's MockTransport:
connecting them, linking an incident, citing and importing rows as leads, the TLP guard, and
detection gaps that only Shikari closes."""

import json

import httpx
import pytest
from sqlalchemy import select

from ostraka.auth.secrets import decrypt
from ostraka.integrations import gaps, http, shikari
from ostraka.models import DetectionGap, Integration, Product, SecurityEvent, Task, Team
from tests.test_incidents import declare, people  # noqa: F401  (the fixture)
from tests.test_licence import accepted

pytestmark = pytest.mark.db

RH = "https://rowanholt.example.org"
RH_OWN = "https://rowanholt.tail180683.ts.net"
SK = "https://shikari.example.org"
SK_OWN = "https://shikari.tail180683.ts.net"
JOB = "8a1f0c2e-5b7d-4c3a-9e21-0f6d2b4a7c11"
ROW = {"ref": "18833", "hash": "f" * 64, "cells": {"EventID": "4624", "IpAddress": "203.0.113.9"}}


class Suite:
    """What the stubbed Rowanholt and Shikari hold, and what Ostraka asked them."""

    def __init__(self) -> None:
        self.down: set[str] = set()
        self.job = {"id": JOB, "case_number": "2026-117", "status": "running", "progress": 40}
        self.findings = [
            {
                "id": "rf1",
                "host": "DC02",
                "artefact": "cooked/evtx/security.jsonl",
                "row_ref": "18833",
                "row_hash": "f" * 64,
                "excerpt": "4624 logon type 10 from 203.0.113.9",
                "title": "RDP logon from 203.0.113.9",
                "technique": "T1021.001",
                "status": "confirmed",
                "intact": True,
            }
        ]
        self.covered: set[str] = {"T1059.001"}
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host in self.down:
            raise httpx.ConnectError("refused", request=request)
        path = request.url.path
        if host.startswith("shikari"):
            if path == "/health":
                return httpx.Response(200, json={"status": "ok", "app": "shikari"})
            if path == "/api/coverage":
                asked = request.url.params["techniques"].split(",")
                return httpx.Response(
                    200,
                    json={
                        "techniques": [
                            {"technique_id": t, "name": f"Name of {t}", "covered": t in self.covered}
                            for t in asked
                        ]
                    },
                )
        else:
            if path == "/api/health":
                return httpx.Response(200, json={"status": "healthy", "timestamp": "2026-11-11T10:00:00"})
            if path == "/api/auth/me":
                return httpx.Response(200, json={"username": "ostraka", "token": {"scope": "aule-mcp"}})
            if path == "/api/jobs":
                return httpx.Response(200, json={"jobs": [self.job], "total": 1})
            if path == f"/api/jobs/{JOB}":
                return httpx.Response(200, json=self.job)
            if path == "/api/validate/case-id/2026-117":
                return httpx.Response(
                    200,
                    json={
                        "exists": True,
                        "case_id": "2026-117",
                        "jobs": [{"id": JOB, "status": self.job["status"], "created_at": "2026-11-10"}],
                    },
                )
            if path.startswith("/api/validate/case-id/"):
                return httpx.Response(200, json={"exists": False, "jobs": []})
            if path == f"/api/jobs/{JOB}/case":
                return httpx.Response(
                    200,
                    json={
                        "hosts": [
                            {"host": "DC02", "files": [{"path": "cooked/evtx/security.jsonl", "size": 1}]}
                        ],
                        "case_files": [{"path": "stacking.jsonl", "size": 1}],
                        "findings": {"confirmed": 1},
                    },
                )
            if path == f"/api/jobs/{JOB}/case/rows":
                return httpx.Response(200, json={"columns": list(ROW["cells"]), "rows": [ROW], "total": 1})
            if path == f"/api/jobs/{JOB}/case/findings":
                return httpx.Response(200, json=self.findings)
        return httpx.Response(404, json={"detail": "Job not found"})

    def asked(self, host: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host and r.url.path == path]


@pytest.fixture
def suite(monkeypatch):
    stub = Suite()
    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(stub.handle))
    return stub


async def connect(client, admin, product: str, url: str, token: str = "tok_secret", **extra) -> dict:  # noqa: S107
    r = await client.put(
        f"/api/integrations/{product}", json={"url": url, "token": token, **extra}, headers=admin.headers
    )
    assert r.status_code == 200, r.text
    return r.json()


async def team_id(db, name: str) -> str:
    return str(await db.scalar(select(Team.id).where(Team.name == name)))


async def finding(
    client, person, incident, techniques=("T1003.001", "T1059.001"), title="LSASS dumped on WS-14"
):
    base = f"/api/incidents/{incident['id']}/findings"
    r = await client.post(
        base,
        json={"kind": "evidence", "title": title, "techniques": list(techniques)},
        headers=person.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def confirm(client, person, incident, found) -> dict:
    base = f"/api/incidents/{incident['id']}/findings/{found['id']}"
    r = await client.post(
        f"{base}/verifications",
        json={"method": "search", "platform": "Defender XDR", "query": "DeviceEvents", "scope": "WS-14"},
        headers=person.headers,
    )
    assert r.status_code == 201, r.text
    r = await client.post(f"{base}/confirm", json={"version": r.json()["version"]}, headers=person.headers)
    assert r.status_code == 200, r.text
    return r.json()


async def gaps_of(client, person, incident) -> dict:
    r = await client.get(f"/api/incidents/{incident['id']}/detection-gaps", headers=person.headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---- connecting the servers ----------------------------------------------------------------------


async def test_admins_connect_rowanholt_with_the_token_encrypted_and_tested(db, client, people, suite):  # noqa: F811
    p = await people()
    r = await client.put(
        "/api/integrations/rowanholt", json={"url": RH, "token": "x"}, headers=p["lead"].headers
    )
    assert r.status_code == 403
    r = await client.put(
        "/api/integrations/rowanholt",
        json={"url": "http://rowanholt.example.org"},
        headers=p["admin"].headers,
    )
    assert r.status_code == 422 and "https" in r.json()["detail"]

    saved = await connect(client, p["admin"], "rowanholt", RH + "/", token="rvt_readonly")
    assert saved["url"] == RH and saved["has_token"] and "token" not in saved
    row = await db.get(Integration, Product.ROWANHOLT)
    await db.refresh(row)
    assert row.token != "rvt_readonly" and decrypt(row.token) == "rvt_readonly"
    assert await db.scalar(select(SecurityEvent).where(SecurityEvent.action == "integration_added"))

    r = await client.post("/api/integrations/rowanholt/test", headers=p["admin"].headers)
    test = r.json()
    assert test["ok"] and [s["ok"] for s in test["steps"]] == [True, True, True]
    assert "read-only" in test["steps"][1]["detail"]
    assert (
        suite.asked("rowanholt.example.org", "/api/auth/me")[0].headers["Authorization"]
        == "Bearer rvt_readonly"
    )

    suite.down.add("rowanholt.example.org")
    test = (await client.post("/api/integrations/rowanholt/test", headers=p["admin"].headers)).json()
    assert not test["ok"] and "can't be reached" in test["message"]
    listed = (await client.get("/api/integrations", headers=p["admin"].headers)).json()
    assert listed["rowanholt"]["check_ok"] is False and listed["shikari"] is None

    # A change carries the version; leaving the token out keeps it
    r = await client.put(
        "/api/integrations/rowanholt", json={"url": RH_OWN, "version": 0}, headers=p["admin"].headers
    )
    assert r.status_code == 409
    r = await client.put(
        "/api/integrations/rowanholt", json={"url": RH_OWN, "version": 1}, headers=p["admin"].headers
    )
    assert r.status_code == 200 and r.json()["has_token"]
    await db.refresh(row)
    assert decrypt(row.token) == "rvt_readonly"


async def test_shikari_webhook_secret_is_shown_once(db, client, people, suite):  # noqa: F811
    p = await people()
    saved = await connect(
        client, p["admin"], "shikari", SK, detection_team_id=await team_id(db, "Threat Hunting")
    )
    assert saved["webhook_secret"] and saved["has_webhook_secret"]
    listed = (await client.get("/api/integrations", headers=p["admin"].headers)).json()
    assert listed["shikari"]["webhook_secret"] is None
    assert listed["shikari_webhook_url"].endswith("/api/integrations/shikari/webhook")
    test = (await client.post("/api/integrations/shikari/test", headers=p["admin"].headers)).json()
    assert test["ok"], test


# ---- Rowanholt on an incident --------------------------------------------------------------------


async def test_linking_a_job_shows_its_status_and_keeps_it_when_rowanholt_is_down(db, client, people, suite):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}/rowanholt"
    view = (await client.get(base, headers=p["soc"].headers)).json()
    assert view["configured"] is False and view["links"] == []

    await connect(client, p["admin"], "rowanholt", RH)
    r = await client.post(f"{base}/links", json={"job_id": JOB}, headers=p["exec"].headers)
    assert r.status_code == 403  # observers can't
    r = await client.post(f"{base}/links", json={"job_id": "0" * 36}, headers=p["soc"].headers)
    assert r.status_code == 404
    r = await client.post(f"{base}/links", json={"job_id": JOB}, headers=p["soc"].headers)
    assert r.status_code == 201, r.text
    [link] = r.json()["links"]
    assert link["status"] == "running" and link["progress"] == 40 and link["case_number"] == "2026-117"
    assert link["url"] == f"{RH}/jobs/{JOB}/case"
    r = await client.post(f"{base}/links", json={"case_number": "2026-117"}, headers=p["soc"].headers)
    assert r.status_code == 201 and r.json()["links"][1]["jobs"][0]["id"] == JOB
    r = await client.post(f"{base}/links", json={"case_number": "nope"}, headers=p["soc"].headers)
    assert r.status_code == 404

    suite.job["status"] = "completed"
    view = (await client.get(base, headers=p["exec"].headers)).json()
    assert view["reachable"] is True and view["links"][0]["status"] == "completed"

    suite.down.add("rowanholt.example.org")
    view = (await client.get(base, headers=p["soc"].headers)).json()
    assert view["reachable"] is False and "last seen" in view["message"]
    assert view["links"][0]["status"] == "completed"
    r = await client.post(f"{base}/links", json={"job_id": "1" * 36}, headers=p["soc"].headers)
    assert r.status_code == 502


async def test_rowanholt_findings_arrive_as_leads_and_only_once(db, client, people, suite):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    await connect(client, p["admin"], "rowanholt", RH)
    base = f"/api/incidents/{incident['id']}/rowanholt"
    link = (await client.post(f"{base}/links", json={"job_id": JOB}, headers=p["soc"].headers)).json()[
        "links"
    ][0]

    [remote] = (await client.get(f"{base}/links/{link['id']}/findings", headers=p["soc"].headers)).json()
    assert remote["status"] == "confirmed" and remote["imported_as"] is None
    r = await client.post(
        f"{base}/import?link_id={link['id']}",
        json={"job_id": JOB, "finding_ids": ["rf1"]},
        headers=p["soc"].headers,
    )
    assert r.status_code == 201, r.text
    [ref] = r.json()["imported"]

    [lead] = (await client.get(f"/api/incidents/{incident['id']}/findings", headers=p["soc"].headers)).json()
    assert lead["ref"] == ref and lead["status"] == "lead"  # never confirmed, whatever Rowanholt said
    assert lead["techniques"] == ["T1021.001"] and lead["verifications"] == []
    assert "where it is confirmed" in lead["statement"]

    again = await client.post(
        f"{base}/import?link_id={link['id']}",
        json={"job_id": JOB, "finding_ids": ["rf1"]},
        headers=p["soc"].headers,
    )
    assert again.json() == {"imported": [], "skipped": 1}
    [remote] = (await client.get(f"{base}/links/{link['id']}/findings", headers=p["soc"].headers)).json()
    assert remote["imported_as"] == ref
    [citation] = (await client.get(base, headers=p["soc"].headers)).json()["citations"]
    assert citation["remote_status"] == "confirmed" and citation["row_ref"] == "18833"


async def test_citing_a_row_can_verify_but_never_confirms(db, client, people, suite):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    await connect(client, p["admin"], "rowanholt", RH)
    base = f"/api/incidents/{incident['id']}/rowanholt"
    link = (await client.post(f"{base}/links", json={"job_id": JOB}, headers=p["soc"].headers)).json()[
        "links"
    ][0]
    case = (await client.get(f"{base}/links/{link['id']}/case", headers=p["soc"].headers)).json()
    assert (
        case["hosts"][0]["files"] == ["cooked/evtx/security.jsonl"] and case["hosts"][1]["host"] == "(case)"
    )
    rows = (
        await client.get(
            f"{base}/links/{link['id']}/rows",
            params={"host": "DC02", "path": "cooked/evtx/security.jsonl"},
            headers=p["exec"].headers,
        )
    ).json()
    assert rows["rows"][0]["cells"]["IpAddress"] == "[ip address]"  # masked for the observer

    found = await finding(client, p["soc"], incident, techniques=())
    cite = {"link_id": link["id"], "host": "DC02", "artefact": "cooked/evtx/security.jsonl"}
    r = await client.post(
        f"/api/incidents/{incident['id']}/findings/{found['id']}/rowanholt",
        json={**cite, "row_ref": "99"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 404
    r = await client.post(
        f"/api/incidents/{incident['id']}/findings/{found['id']}/rowanholt",
        json={**cite, "row_ref": "18833", "verify": True},
        headers=p["lead"].headers,
    )
    assert r.status_code == 201, r.text
    citation = r.json()
    assert citation["row_hash"] == "f" * 64 and citation["excerpt"]["EventID"] == "4624"
    assert citation["url"] == f"{RH}/jobs/{JOB}/case?host=DC02&path=cooked%2Fevtx%2Fsecurity.jsonl&row=18833"

    now = (
        await client.get(f"/api/incidents/{incident['id']}/findings/{found['id']}", headers=p["soc"].headers)
    ).json()
    assert now["status"] == "lead"
    [v] = now["verifications"]
    assert v["method"] == "rowanholt" and v["ran_by"]["name"] == "Lee Lead" and "row 18833" in v["reference"]
    r = await client.post(
        f"/api/incidents/{incident['id']}/findings/{found['id']}/confirm",
        json={"version": now["version"]},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "confirmed"


async def test_tlp_guard_keeps_a_red_incident_to_a_rowanholt_on_your_own_network(db, client, people, suite):  # noqa: F811
    p = await people()
    red = await declare(client, p["soc"], tlp="red")
    await connect(client, p["admin"], "rowanholt", RH)
    base = f"/api/incidents/{red['id']}/rowanholt"
    r = await client.post(f"{base}/links", json={"job_id": JOB}, headers=p["soc"].headers)
    assert r.status_code == 403 and "TLP:RED" in r.json()["detail"]
    assert suite.requests == []  # nothing was asked

    row = await db.get(Integration, Product.ROWANHOLT)
    await connect(client, p["admin"], "rowanholt", RH_OWN, version=row.version)
    r = await client.post(f"{base}/links", json={"job_id": JOB}, headers=p["soc"].headers)
    assert r.status_code == 201 and suite.requests[-1].url.host == "rowanholt.tail180683.ts.net"


# ---- detection gaps ------------------------------------------------------------------------------


async def test_confirming_opens_a_gap_with_a_task_for_each_undetected_technique(db, client, people, suite):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    found = await finding(client, p["soc"], incident)
    await confirm(client, p["soc"], incident, found)
    assert (await gaps_of(client, p["soc"], incident)) == {
        "configured": False,
        "automation": False,
        "coverage_url": None,
        "message": "",
        "gaps": [],
    }  # no Shikari: nothing tracked, nothing asked
    assert suite.requests == []

    await connect(client, p["admin"], "shikari", SK, detection_team_id=await team_id(db, "Threat Hunting"))
    second = await finding(client, p["soc"], incident, title="Second host")
    await confirm(client, p["soc"], incident, second)
    listed = await gaps_of(client, p["soc"], incident)
    by_technique = {g["technique"]: g for g in listed["gaps"]}
    assert by_technique["T1059.001"]["status"] == "closed" and by_technique["T1059.001"]["task"] is None
    gap = by_technique["T1003.001"]
    assert gap["status"] == "open" and gap["technique_name"] == "Name of T1003.001"
    assert gap["task"]["title"].startswith("Detection for T1003.001") and gap["task"]["status"] == "open"
    task = await db.get(Task, gap["task"]["id"])
    assert (await db.get(Team, task.team_id)).name == "Threat Hunting"
    detail = (await client.get(f"/api/incidents/{incident['id']}", headers=p["soc"].headers)).json()
    assert "Threat Hunting" in [t["name"] for t in detail["teams"]]

    # The earlier finding's techniques are picked up by asking again
    suite.covered = set()
    r = await client.post(f"/api/incidents/{incident['id']}/detection-gaps/check", headers=p["soc"].headers)
    assert r.status_code == 200 and "no new detections" in r.json()["message"]


async def test_a_gap_closes_only_when_shikari_has_a_detection(db, client, people, suite):  # noqa: F811
    p = await people()
    await connect(client, p["admin"], "shikari", SK, detection_team_id=await team_id(db, "Threat Hunting"))
    incident = await declare(client, p["soc"])
    await confirm(client, p["soc"], incident, await finding(client, p["soc"], incident))
    gap = next(g for g in (await gaps_of(client, p["soc"], incident))["gaps"] if g["status"] == "open")

    # Finishing the task doesn't close the gap
    task = await db.get(Task, gap["task"]["id"])
    r = await client.patch(
        f"/api/incidents/{incident['id']}/tasks/{task.id}",
        json={"version": task.version, "status": "done"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text
    assert (
        next(g for g in (await gaps_of(client, p["soc"], incident))["gaps"] if g["id"] == gap["id"])["status"]
        == "open"
    )
    check = f"/api/incidents/{incident['id']}/detection-gaps/check"
    assert (await client.post(check, headers=p["exec"].headers)).status_code == 403

    suite.covered.add("T1003.001")
    r = await client.post(check, headers=p["soc"].headers)
    assert "closed" in r.json()["message"]
    closed = next(g for g in r.json()["gaps"] if g["id"] == gap["id"])
    assert closed["status"] == "closed" and "Sam Soc" in closed["closed_by"]


async def test_when_shikari_is_down_the_gap_opens_and_is_checked_later(db, client, people, suite):  # noqa: F811
    p = await people()
    await connect(client, p["admin"], "shikari", SK, detection_team_id=await team_id(db, "Threat Hunting"))
    incident = await declare(client, p["soc"])
    suite.down.add("shikari.example.org")
    await confirm(client, p["soc"], incident, await finding(client, p["soc"], incident))
    listed = (await gaps_of(client, p["soc"], incident))["gaps"]
    assert {g["status"] for g in listed} == {"open"} and len(listed) == 2  # T1059.001 unknown, so open
    assert all("can't be reached" in g["check_error"] and g["task"] for g in listed)

    suite.down.clear()
    r = await client.post(f"/api/incidents/{incident['id']}/detection-gaps/check", headers=p["soc"].headers)
    states = {g["technique"]: (g["status"], g["task"]["status"]) for g in r.json()["gaps"]}
    assert states == {"T1003.001": ("open", "open"), "T1059.001": ("closed", "done")}  # its task finished too


async def test_a_red_incident_gap_waits_for_someone_to_give_it_a_task(db, client, people, suite):  # noqa: F811
    p = await people()
    await connect(
        client, p["admin"], "shikari", SK_OWN, detection_team_id=await team_id(db, "Threat Hunting")
    )
    red = await declare(client, p["soc"], tlp="red")
    await confirm(client, p["soc"], red, await finding(client, p["soc"], red, techniques=("T1003.001",)))
    [gap] = (await gaps_of(client, p["soc"], red))["gaps"]
    assert gap["status"] == "open" and gap["task"] is None
    detail = (await client.get(f"/api/incidents/{red['id']}", headers=p["soc"].headers)).json()
    assert "Threat Hunting" not in [t["name"] for t in detail["teams"]]  # not brought in automatically

    r = await client.post(
        f"/api/incidents/{red['id']}/detection-gaps/{gap['id']}/task",
        json={"assignee_id": str(p["soc"].id)},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["gaps"][0]["task"]["title"].startswith("Detection for T1003.001")
    again = await client.post(
        f"/api/incidents/{red['id']}/detection-gaps/{gap['id']}/task",
        json={"assignee_id": str(p["soc"].id)},
        headers=p["soc"].headers,
    )
    assert again.status_code == 409


async def test_the_list_across_incidents_shows_only_what_you_can_see(db, client, people, suite):  # noqa: F811
    p = await people()
    await connect(client, p["admin"], "shikari", SK, detection_team_id=await team_id(db, "Threat Hunting"))
    mine = await declare(client, p["dfir"], title="Forensics only")
    await confirm(client, p["dfir"], mine, await finding(client, p["dfir"], mine, techniques=("T1003.001",)))
    seen = (await client.get("/api/detection-gaps", headers=p["dfir"].headers)).json()
    assert [(g["incident"]["ref"], g["technique"]) for g in seen] == [(mine["ref"], "T1003.001")]
    assert (await client.get("/api/detection-gaps", headers=p["soc"].headers)).json() == []
    assert (await client.get("/api/detection-gaps?status=closed", headers=p["dfir"].headers)).json() == []
    assert len((await client.get("/api/detection-gaps?status=all", headers=p["admin"].headers)).json()) == 1


# ---- Shikari's webhook ---------------------------------------------------------------------------


def delivery(secret: str, event: str = "run.completed", **data) -> tuple[bytes, dict[str, str]]:
    body = json.dumps({"event": event, "sent_at": 1762855200.5, "data": {"run": "FIN7", **data}}).encode()
    return body, {
        "Content-Type": "application/json",
        "X-Shikari-Event": event,
        "X-Shikari-Signature": shikari.sign(secret, body),
    }


async def test_shikari_webhook_is_verified_and_acted_on_with_automation(
    db,
    client,
    people,  # noqa: F811
    suite,
    licence_line,
):
    p = await people()
    secret = (
        await connect(
            client, p["admin"], "shikari", SK, detection_team_id=await team_id(db, "Threat Hunting")
        )
    )["webhook_secret"]
    incident = await declare(client, p["soc"])
    await confirm(
        client, p["soc"], incident, await finding(client, p["soc"], incident, techniques=("T1003.001",))
    )
    url = "/api/integrations/shikari/webhook"

    body, headers = delivery("wrong")
    assert (await client.post(url, content=body, headers=headers)).status_code == 401
    assert (
        await client.post(url, content=body, headers={"Content-Type": "application/json"})
    ).status_code == 401
    assert await db.scalar(select(SecurityEvent).where(SecurityEvent.action == "shikari_webhook_refused"))

    suite.covered.add("T1003.001")
    body, headers = delivery(secret)
    r = await client.post(url, content=body, headers=headers)
    assert r.status_code == 202 and r.json()["accepted"] is False and "Pro" in r.json()["reason"]
    await gaps.wait()
    assert (
        await db.scalar(select(DetectionGap).where(DetectionGap.technique == "T1003.001"))
    ).status.value == "open"

    r = await client.put("/api/licence", json=accepted(licence_line("pro")), headers=p["admin"].headers)
    assert r.status_code == 200, r.text
    body, headers = delivery(secret, event="watchlist.match")
    assert (await client.post(url, content=body, headers=headers)).json()["accepted"] is False
    body, headers = delivery(secret)
    r = await client.post(url, content=body, headers=headers)
    assert r.status_code == 202 and r.json() == {"accepted": True}
    await gaps.wait()
    [gap] = (await gaps_of(client, p["soc"], incident))["gaps"]
    assert gap["status"] == "closed" and gap["closed_by"] == "Shikari run FIN7 completed"
    assert gap["task"]["status"] == "done"
