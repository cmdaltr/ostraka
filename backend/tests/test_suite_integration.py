"""The suite's contract (docs/suite/INTEGRATION.md) from Ostraka's side, against Postgres, with Rowanholt and
Shikari stubbed: pairing, starting a Rowanholt job and a Shikari hunt from a record, and what their
webhooks do."""

import json

import httpx
import pytest
from samarkand.webhooks import sign
from sqlalchemy import select

from ostraka.auth.secrets import decrypt
from ostraka.integrations import events, http
from ostraka.models import (
    ActivityEvent,
    Finding,
    Integration,
    Product,
    RowanholtCitation,
    RowanholtLink,
    SecurityEvent,
    ShikariRun,
)
from tests.test_incidents import declare, people  # noqa: F401  (the fixture)
from tests.test_integrations import JOB, RH, RH_OWN, SK, Suite, connect
from tests.test_licence import accepted

pytestmark = pytest.mark.db

CODE = "pair_" + "k" * 43
RUN = "6f1c2a7e-0d4b-4c52-9a8e-2b7d1e0f3c99"


class Siblings(Suite):
    """The stubbed suite, with the contract's pairing, job and hunt-run endpoints."""

    def __init__(self) -> None:
        super().__init__()
        self.claim: dict | None = None
        self.claim_status = 200
        self.started: dict | None = None
        self.start_status = 201
        self.run = {"id": RUN, "status": "generating", "group_name": "techniques-T1059.001"}

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        shikari = request.url.host.startswith("shikari")
        if method == "POST" and path == "/api/pairing/claim":
            self.requests.append(request)
            self.claim = json.loads(request.content)
            if self.claim_status != 200:
                return httpx.Response(
                    self.claim_status, json={"detail": "This pairing link isn't valid. Ask for a new one."}
                )
            return httpx.Response(
                200,
                json={
                    "product": "shikari" if shikari else "rowanholt",
                    "version": "2.2.0",
                    "token": "shik_paired" if shikari else "rvt_paired",
                    "token_expires_at": "2027-11-23T00:00:00Z",
                    "webhook_id": "wh1",
                    "webhook_secret": "sibling-secret",
                    "events": ["run.completed", "run.failed", "run.executed"]
                    if shikari
                    else ["job.completed", "job.failed", "finding.confirmed"],
                },
            )
        if method == "POST" and path == "/api/jobs" and not shikari:
            self.requests.append(request)
            self.started = json.loads(request.content)
            if self.start_status != 201:
                return httpx.Response(self.start_status, json={"detail": "Not allowed for this token."})
            return httpx.Response(
                201, json={"id": JOB, "case_number": self.started["case_number"], "status": "pending"}
            )
        if method == "POST" and path == "/api/runs" and shikari:
            self.requests.append(request)
            self.started = json.loads(request.content)
            return httpx.Response(202, json={**self.run, "status": "queued"})
        if path == f"/api/runs/{RUN}" and shikari:
            self.requests.append(request)
            return httpx.Response(200, json=self.run)
        return super().handle(request)


@pytest.fixture
def siblings(monkeypatch):
    stub = Siblings()
    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(stub.handle))
    return stub


async def pair(client, admin, origin: str, code: str = CODE):
    return await client.post(
        "/api/integrations/pair", json={"link": f"{origin}/pair#{code}"}, headers=admin.headers
    )


async def pro(client, admin, licence_line) -> None:
    r = await client.put("/api/licence", json=accepted(licence_line("pro")), headers=admin.headers)
    assert r.status_code == 200, r.text


def delivery(secret: str, product: str, event: str, **data) -> tuple[bytes, dict[str, str]]:
    body = json.dumps({"event": event, "sent_at": "2026-11-23T10:00:00Z", "data": data}).encode()
    prefix = "X-Rowanholt" if product == "rowanholt" else "X-Shikari"
    return body, {
        "Content-Type": "application/json",
        f"{prefix}-Event": event,
        f"{prefix}-Signature": sign(secret, body),
    }


async def actions(db, incident) -> list[str]:
    return list(
        (
            await db.scalars(
                select(ActivityEvent.summary)
                .where(ActivityEvent.incident_id == incident["id"])
                .order_by(ActivityEvent.position)
            )
        ).all()
    )


# ---- pairing --------------------------------------------------------------------------------------


async def test_pairing_with_rowanholt_stores_the_token_and_secret_it_made(db, client, people, siblings):  # noqa: F811
    p = await people()
    assert (await pair(client, p["lead"], RH)).status_code == 403  # admins only

    siblings.scope = "ostraka"
    r = await pair(client, p["admin"], RH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["integration"]["product"] == "rowanholt" and out["integration"]["url"] == RH
    assert out["test"]["ok"] is True and "starts jobs" in out["test"]["steps"][1]["detail"]
    assert out["events"] == ["job.completed", "job.failed", "finding.confirmed"]
    assert "rvt_paired" not in r.text and "sibling-secret" not in r.text

    # The claim carried the code and where Rowanholt's webhooks should go, and nothing else of Ostraka's
    assert siblings.claim == {
        "code": CODE,
        "product": "ostraka",
        "name": "Ostraka",
        "version": siblings.claim["version"],
        "webhook_url": "http://ostraka.test/api/integrations/rowanholt/webhook",
    }
    row = await db.get(Integration, Product.ROWANHOLT)
    assert decrypt(row.token) == "rvt_paired" and decrypt(row.webhook_secret) == "sibling-secret"
    assert "rvt_paired" not in row.token  # encrypted at rest
    event = await db.scalar(select(SecurityEvent).where(SecurityEvent.action == "integration_paired"))
    assert event.detail["product"] == "rowanholt" and "rvt_paired" not in json.dumps(event.detail)


async def test_pairing_tells_shikari_from_rowanholt_by_asking(db, client, people, siblings):  # noqa: F811
    p = await people()
    r = await pair(client, p["admin"], SK)
    assert r.status_code == 200, r.text
    assert r.json()["integration"]["product"] == "shikari"
    assert siblings.claim["webhook_url"].endswith("/api/integrations/shikari/webhook")
    assert decrypt((await db.get(Integration, Product.SHIKARI)).token) == "shik_paired"


async def test_a_used_or_mangled_link_pairs_nothing(db, client, people, siblings):  # noqa: F811
    p = await people()
    r = await pair(client, p["admin"], RH, code="not-a-code")
    assert r.status_code == 422 and "pairing link" in r.json()["detail"]
    assert siblings.requests == []  # nothing was asked of anyone

    siblings.claim_status = 404
    r = await pair(client, p["admin"], RH)
    assert r.status_code == 422 and "isn't valid" in r.json()["detail"]
    assert await db.get(Integration, Product.ROWANHOLT) is None
    assert await db.scalar(select(SecurityEvent).where(SecurityEvent.action == "integration_pairing_failed"))

    siblings.down.add("rowanholt.example.org")
    r = await pair(client, p["admin"], RH)
    assert r.status_code == 422 and "doesn't answer as Rowanholt or Shikari" in r.json()["detail"]


# ---- starting a Rowanholt job ---------------------------------------------------------------------


async def test_a_rowanholt_job_starts_for_the_record_and_is_linked(db, client, people, siblings):  # noqa: F811
    p = await people()
    await pair(client, p["admin"], RH)
    incident = await declare(client, p["soc"])
    url = f"/api/incidents/{incident['id']}/rowanholt/jobs"
    body = {
        "case_number": "2026-117",
        "source_paths": ["/evidence/DC02.E01", "  ", "/evidence/WS14.E01"],
        "options": {"timeline": True, "yara": False, "force_overwrite": True},
    }
    assert (await client.post(url, json=body, headers=p["exec"].headers)).status_code == 403

    r = await client.post(url, json=body, headers=p["soc"].headers)
    assert r.status_code == 201, r.text
    assert siblings.started == {
        "case_number": "2026-117",
        "source_paths": ["/evidence/DC02.E01", "/evidence/WS14.E01"],
        "options": {"timeline": True, "yara": False},  # only the switches Ostraka offers
        "external_ref": incident["ref"],
    }
    [link] = r.json()["links"]
    assert link["job_id"] == JOB and link["case_number"] == "2026-117"
    assert any("started Rowanholt job" in a for a in await actions(db, incident))


async def test_a_read_only_token_cant_start_jobs_and_says_to_pair(db, client, people, siblings):  # noqa: F811
    p = await people()
    await connect(client, p["admin"], "rowanholt", RH)
    siblings.start_status = 403
    incident = await declare(client, p["soc"])
    r = await client.post(
        f"/api/incidents/{incident['id']}/rowanholt/jobs",
        json={"case_number": "2026-117", "source_paths": ["/evidence/DC02.E01"]},
        headers=p["soc"].headers,
    )
    assert r.status_code == 403 and "Pair Ostraka with Rowanholt again" in r.json()["detail"]
    assert await db.scalar(select(RowanholtLink.id)) is None


async def test_the_tlp_guard_keeps_a_red_record_off_a_public_rowanholt(db, client, people, siblings):  # noqa: F811
    p = await people()
    await pair(client, p["admin"], RH)
    incident = await declare(client, p["soc"], tlp="red")
    r = await client.post(
        f"/api/incidents/{incident['id']}/rowanholt/jobs",
        json={"case_number": "2026-117", "source_paths": ["/evidence/DC02.E01"]},
        headers=p["soc"].headers,
    )
    assert r.status_code == 403 and "TLP:RED" in r.json()["detail"]
    assert siblings.started is None


# ---- Rowanholt's webhooks -------------------------------------------------------------------------


async def test_rowanholt_says_a_job_finished_and_its_record_hears(
    db,
    client,
    people,  # noqa: F811
    siblings,
    licence_line,
):
    p = await people()
    await pair(client, p["admin"], RH)
    incident = await declare(client, p["soc"])
    await client.post(
        f"/api/incidents/{incident['id']}/rowanholt/jobs",
        json={"case_number": "2026-117", "source_paths": ["/evidence/DC02.E01"]},
        headers=p["soc"].headers,
    )
    url = "/api/integrations/rowanholt/webhook"
    data = {"job_id": JOB, "case_number": "2026-117", "status": "completed", "external_ref": incident["ref"]}

    body, headers = delivery("wrong", "rowanholt", "job.completed", **data)
    assert (await client.post(url, content=body, headers=headers)).status_code == 401
    assert await db.scalar(select(SecurityEvent).where(SecurityEvent.action == "rowanholt_webhook_refused"))

    body, headers = delivery("sibling-secret", "rowanholt", "job.completed", **data)
    r = await client.post(url, content=body, headers=headers)
    assert r.status_code == 202 and r.json()["accepted"] is False and "Pro" in r.json()["reason"]

    await pro(client, p["admin"], licence_line)
    r = await client.post(url, content=body, headers=headers)
    assert r.json() == {"accepted": True}
    await events.wait()
    assert f"Rowanholt job {JOB} (case 2026-117) completed" in await actions(db, incident)
    assert (await db.scalar(select(RowanholtLink))).status == "completed"

    body, headers = delivery("sibling-secret", "rowanholt", "report.ready", job_id=JOB)
    assert (await client.post(url, content=body, headers=headers)).json()["accepted"] is False


async def test_a_finding_confirmed_in_rowanholt_arrives_once_as_a_lead(
    db,
    client,
    people,  # noqa: F811
    siblings,
    licence_line,
):
    p = await people()
    await pair(client, p["admin"], RH)
    await pro(client, p["admin"], licence_line)
    incident = await declare(client, p["soc"])
    body, headers = delivery(
        "sibling-secret",
        "rowanholt",
        "finding.confirmed",
        job_id=JOB,
        case_number="2026-117",
        finding_id="rf1",
        host="DC02",
        technique="T1021.001",
        external_ref=incident["ref"],
    )
    for _ in range(2):  # a webhook delivered twice brings it in once
        r = await client.post("/api/integrations/rowanholt/webhook", content=body, headers=headers)
        assert r.json() == {"accepted": True}
        await events.wait()

    [finding] = (await db.scalars(select(Finding).where(Finding.incident_id == incident["id"]))).all()
    assert finding.status.value == "lead" and finding.created_by is None
    assert finding.techniques == ["T1021.001"] and "where it is confirmed" in finding.statement
    citation = await db.scalar(select(RowanholtCitation))
    assert (citation.host, citation.row_ref, citation.remote_finding_id) == ("DC02", "18833", f"{JOB}:rf1")
    assert any(
        a.startswith("Rowanholt (confirmed there) brought in FND-") for a in await actions(db, incident)
    )


async def test_a_webhook_for_a_red_record_on_a_public_rowanholt_changes_nothing(
    db,
    client,
    people,  # noqa: F811
    siblings,
    licence_line,
):
    p = await people()
    await pair(client, p["admin"], RH)
    await pro(client, p["admin"], licence_line)
    incident = await declare(client, p["soc"], tlp="red")
    body, headers = delivery(
        "sibling-secret",
        "rowanholt",
        "finding.confirmed",
        job_id=JOB,
        finding_id="rf1",
        external_ref=incident["ref"],
    )
    await client.post("/api/integrations/rowanholt/webhook", content=body, headers=headers)
    await events.wait()
    assert await db.scalar(select(Finding.id).where(Finding.incident_id == incident["id"])) is None


async def test_own_network_rowanholt_may_serve_a_red_record(db, client, people, siblings, licence_line):  # noqa: F811
    p = await people()
    r = await pair(client, p["admin"], RH_OWN)
    assert r.status_code == 200, r.text
    await pro(client, p["admin"], licence_line)
    incident = await declare(client, p["soc"], tlp="red")
    body, headers = delivery(
        "sibling-secret",
        "rowanholt",
        "finding.confirmed",
        job_id=JOB,
        finding_id="rf1",
        external_ref=incident["ref"],
    )
    await client.post("/api/integrations/rowanholt/webhook", content=body, headers=headers)
    await events.wait()
    assert await db.scalar(select(Finding.id).where(Finding.incident_id == incident["id"])) is not None


# ---- Shikari hunt runs ----------------------------------------------------------------------------


async def test_a_hunt_starts_a_shikari_run_and_its_webhooks_bring_back_counts_only(
    db,
    client,
    people,  # noqa: F811
    siblings,
    licence_line,
):
    p = await people()
    await pair(client, p["admin"], SK)
    hunt = (
        await client.post(
            "/api/incidents",
            json={"title": "PowerShell cradles", "kind": "hunt", "severity": "medium", "tlp": "amber"},
            headers=p["soc"].headers,
        )
    ).json()
    url = f"/api/incidents/{hunt['id']}/shikari/runs"
    body = {"kind": "techniques", "target": "t1059.001 T1003.001", "execute": True, "window_days": 14}
    assert (await client.post(url, json=body, headers=p["exec"].headers)).status_code == 403
    assert (
        await client.post(url, json={**body, "window_days": 365}, headers=p["soc"].headers)
    ).status_code == 422

    r = await client.post(url, json=body, headers=p["soc"].headers)
    assert r.status_code == 201, r.text
    assert siblings.started == {
        "kind": "techniques",
        "target": "T1059.001,T1003.001",
        "external_ref": hunt["ref"],
        "execute": True,
        "window_days": 14,
    }
    assert r.json()["status"] == "queued" and r.json()["run_id"] == RUN

    # Shikari ran the queries; its answer carries a row it shouldn't, which Ostraka doesn't keep
    siblings.run = {
        "id": RUN,
        "status": "executed",
        "group_name": "techniques-T1059.001",
        "detections": [
            {
                "file": "T1059.001.yaml",
                "name": "Encoded PowerShell",
                "techniques": ["T1059.001"],
                "platforms": ["splunk"],
                "queries": [{"platform": "splunk", "query": "index=wineventlog EventCode=4104"}],
            }
        ],
        "counts": [
            {"file": "T1059.001.yaml", "technique": "T1059.001", "platform": "splunk", "hits": 12},
            {"file": "T1059.001.yaml", "platform": "elastic", "skipped": True, "rows": [{"host": "WS-14"}]},
        ],
        "totals": {"queries": 2, "run": 1, "hits": 12, "errors": 0, "skipped": 1},
    }
    await pro(client, p["admin"], licence_line)
    webhook = "/api/integrations/shikari/webhook"
    payload, headers = delivery(
        "sibling-secret", "shikari", "run.executed", run_id=RUN, external_ref=hunt["ref"], group_name="x"
    )
    assert (await client.post(webhook, content=payload, headers=headers)).json() == {"accepted": True}
    await events.wait()

    [run] = (await client.get(url, headers=p["exec"].headers)).json()  # observers see the counts
    assert run["status"] == "executed" and run["totals"]["hits"] == 12
    assert [c["hits"] for c in run["counts"]] == [12, None] and run["counts"][1]["skipped"] is True
    assert "WS-14" not in json.dumps(run)
    stored = await db.scalar(select(ShikariRun))
    assert "WS-14" not in json.dumps(stored.counts) + json.dumps(stored.detections)
    assert (
        "Shikari's hunt for techniques T1059.001,T1003.001 ran 1 of 2 queries: 12 results, "
        "1 had no SIEM connection"
    ) in await actions(db, hunt)

    # A run Ostraka didn't start is none of its business
    payload, headers = delivery("sibling-secret", "shikari", "run.completed", run_id="someone-elses")
    assert (await client.post(webhook, content=payload, headers=headers)).json() == {"accepted": True}
    await events.wait()


async def test_refresh_asks_shikari_without_waiting_for_a_webhook(db, client, people, siblings):  # noqa: F811
    p = await people()
    await pair(client, p["admin"], SK)
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}/shikari/runs"
    run = (
        await client.post(base, json={"kind": "group", "target": "APT29"}, headers=p["soc"].headers)
    ).json()
    siblings.run["status"] = "generated"
    r = await client.post(f"{base}/{run['id']}/refresh", headers=p["soc"].headers)
    assert r.status_code == 200 and r.json()["status"] == "generated"
    assert r.json()["link"] == f"{SK}/results/techniques-T1059.001"
