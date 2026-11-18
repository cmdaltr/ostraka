"""SIEM queries against Postgres: connecting one (admins only, secrets encrypted and audited), running a
query for a record or for none, keeping the query and never its rows, and finding queries again by any
part of their text, as far as each viewer's access goes."""

import pytest
from samarkand.siem import Result, SiemProblem
from sqlalchemy import select, text

from ostraka import siem
from ostraka.models import ActivityEvent, SecurityEvent, SiemConnection, SiemQuery
from tests.test_incidents import declare, people  # noqa: F401

pytestmark = pytest.mark.db

SPLUNK = {
    "name": "Splunk prod",
    "kind": "splunk",
    "values": {"url": "https://splunk.example:8089", "token": "t0k"},
}


class Stub:
    """A SIEM that answers from a script: the rows to return, or a problem to raise."""

    def __init__(self, answer):
        self.answer, self.seen = answer, []

    async def query_async(self, query, **window):
        self.seen.append((query, window))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    async def check_async(self):
        return await self.query_async("| makeresults")


@pytest.fixture
def siem_answers(monkeypatch):
    stub = Stub(Result([{"user": "bob", "src_ip": "203.0.113.7"}, {"user": "bob"}]))
    monkeypatch.setattr(siem, "client_for", lambda row: stub)
    return stub


async def connect(client, person, body=SPLUNK) -> dict:
    r = await client.post("/api/siem/connections", json=body, headers=person.headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_admins_connect_a_siem_its_secret_encrypted_and_never_shown(db, client, people):  # noqa: F811
    p = await people()
    assert (
        await client.post("/api/siem/connections", json=SPLUNK, headers=p["lead"].headers)
    ).status_code == 403
    made = await connect(client, p["admin"])
    assert made["settings"] == {"url": "https://splunk.example:8089"} and made["secrets_set"] == ["token"]
    assert "t0k" not in str(made)
    stored = await db.scalar(select(SiemConnection.secret))
    assert stored and "t0k" not in stored
    assert siem.secrets_of(await db.scalar(select(SiemConnection))) == {"token": "t0k"}

    # anyone else sees the enabled connections, without their settings
    seen = (await client.get("/api/siem/connections", headers=p["soc"].headers)).json()
    assert seen == [
        {
            "id": made["id"], "name": "Splunk prod", "kind": "splunk", "language": "SPL", "enabled": True,
            "settings": None, "secrets_set": None, "verify_tls": None, "checked_at": None, "check_ok": None,
            "check_message": "", "version": None,
        }
    ]  # fmt: skip

    # a secret left empty keeps the one stored
    r = await client.patch(
        f"/api/siem/connections/{made['id']}",
        json={"version": 1, "values": {"url": "https://splunk2.example:8089", "token": ""}},
        headers=p["admin"].headers,
    )
    assert r.status_code == 200 and r.json()["secrets_set"] == ["token"]
    r = await client.post("/api/siem/connections", json={**SPLUNK, "values": {}}, headers=p["admin"].headers)
    assert r.status_code == 409  # the name's taken
    r = await client.post(
        "/api/siem/connections", json={**SPLUNK, "name": "Other", "values": {"url": "https://x:8089"}},
        headers=p["admin"].headers,
    )  # fmt: skip
    assert r.status_code == 422 and "token, or a username" in r.json()["detail"]

    actions = (
        await db.scalars(select(SecurityEvent.action).where(SecurityEvent.action.like("siem_%")))
    ).all()
    assert actions == ["siem_connected", "siem_changed"]


async def test_a_connection_test_keeps_its_answer(db, client, people, siem_answers):  # noqa: F811
    p = await people()
    made = await connect(client, p["admin"])
    r = await client.post(f"/api/siem/connections/{made['id']}/test", headers=p["admin"].headers)
    assert r.json()["check_ok"] is True
    siem_answers.answer = SiemProblem("Splunk refused the credentials (token expired or wrong?): bad token")
    r = await client.post(f"/api/siem/connections/{made['id']}/test", headers=p["admin"].headers)
    assert r.json()["check_ok"] is False and "bad token" in r.json()["check_message"]


async def test_a_query_is_kept_and_its_rows_are_not(db, client, people, siem_answers):  # noqa: F811
    p = await people()
    made = await connect(client, p["admin"])
    incident = await declare(client, p["soc"])
    r = await client.post(
        "/api/siem/queries",
        json={
            "connection_id": made["id"],
            "text": " index=mail user=bob src_ip=203.0.113.7 ",
            "incident_id": incident["id"],
            "start": "2026-11-17T00:00:00Z",
        },
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["rows"] == [{"user": "bob", "src_ip": "203.0.113.7"}, {"user": "bob"}]
    assert run["columns"] == ["user", "src_ip"] and run["problem"] == ""
    assert run["query"]["text"] == "index=mail user=bob src_ip=203.0.113.7"
    assert run["query"]["row_count"] == 2 and run["query"]["outcome"] == "ok"
    assert run["query"]["incident"]["ref"] == incident["ref"] and run["query"]["ref"].startswith("QRY-")
    query, window = siem_answers.seen[0]
    assert query == "index=mail user=bob src_ip=203.0.113.7" and window["max_rows"] == siem.MAX_ROWS
    assert window["start"].isoformat() == "2026-11-17T00:00:00+00:00"

    # nothing of the rows is in the database: not the query table, not the activity log
    dump = (await db.execute(text("SELECT row_to_json(q)::text FROM siem_queries q"))).scalars().all()
    assert len(dump) == 1 and '"user"' not in dump[0] and 'bob"' not in dump[0].split('"text"')[0]
    summaries = (
        await db.scalars(select(ActivityEvent.summary).where(ActivityEvent.action == "query_run"))
    ).all()
    assert summaries == [f"Sam Soc ran {run['query']['ref']} on Splunk prod: 2 rows"]


async def test_a_refused_query_is_kept_as_failed_with_the_reason_in_the_answer_only(
    db,
    client,
    people,  # noqa: F811
    siem_answers,
):
    p = await people()
    made = await connect(client, p["admin"])
    siem_answers.answer = SiemProblem("Unknown search command 'frob'.")
    r = await client.post(
        "/api/siem/queries", json={"connection_id": made["id"], "text": "| frob"}, headers=p["soc"].headers
    )
    assert r.status_code == 200
    assert r.json()["problem"] == "Unknown search command 'frob'." and r.json()["rows"] == []
    row = await db.scalar(select(SiemQuery))
    assert row.outcome.value == "failed" and row.row_count is None and row.incident_id is None


async def test_who_may_run_a_query(db, client, people, siem_answers):  # noqa: F811
    p = await people()
    made = await connect(client, p["admin"])
    incident = await declare(client, p["soc"])
    body = {"connection_id": made["id"], "text": "index=x"}
    assert (await client.post("/api/siem/queries", json=body, headers=p["exec"].headers)).status_code == 403
    # only for a record they can work
    r = await client.post(
        "/api/siem/queries", json={**body, "incident_id": incident["id"]}, headers=p["dfir"].headers
    )
    assert r.status_code == 404
    await client.patch(
        f"/api/siem/connections/{made['id']}",
        json={"version": 1, "enabled": False},
        headers=p["admin"].headers,
    )
    assert (await client.post("/api/siem/queries", json=body, headers=p["soc"].headers)).status_code == 404


async def test_queries_are_found_by_any_part_of_their_text_as_far_as_access_goes(
    db,
    client,
    people,  # noqa: F811
    siem_answers,
):
    p = await people()
    made = await connect(client, p["admin"])
    incident = await declare(client, p["soc"])

    async def run(person, query, incident_id=None):
        body = {"connection_id": made["id"], "text": query, "incident_id": incident_id}
        assert (await client.post("/api/siem/queries", json=body, headers=person.headers)).status_code == 200

    await run(p["soc"], "index=proxy dest=203.0.113.7", incident["id"])
    await run(p["soc"], 'index=mail subject="Invoice 50%_off"')
    await run(p["dfir"], "index=edr process=rclone.exe")

    async def search(person, **params):
        r = await client.get("/api/siem/queries", params=params, headers=person.headers)
        assert r.status_code == 200, r.text
        return [q["text"] for q in r.json()]

    assert await search(p["soc"], q="203.0.113") == ["index=proxy dest=203.0.113.7"]
    assert await search(p["soc"], q="RCLONE") == ["index=edr process=rclone.exe"]  # any case, anyone's
    assert await search(p["soc"], q="50%_") == ['index=mail subject="Invoice 50%_off"']  # taken literally
    assert await search(p["soc"], q="index", mine=True) == [
        'index=mail subject="Invoice 50%_off"',
        "index=proxy dest=203.0.113.7",
    ]
    assert await search(p["soc"], incident_id=incident["id"]) == ["index=proxy dest=203.0.113.7"]
    # Forensics can't see the record, so not the query run for it
    assert "index=proxy dest=203.0.113.7" not in await search(p["dfir"], q="index")
    # an observer sees indicators masked, and can't find a query by one
    assert await search(p["exec"], q="203.0.113") == []
    masked = await search(p["exec"], q="proxy")
    assert masked and "203.0.113.7" not in masked[0]
