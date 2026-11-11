"""The MCP server at /mcp: only API tokens get in, each call acts as the token's owner through ostraka's
own API, read-only tokens see only the read tools, and restricted TLP stays inside ostraka."""

import json

import pytest

from ostraka.models import Role

pytestmark = pytest.mark.db

ACCEPT = "application/json, text/event-stream"


async def token(client, person, scope: str) -> str:
    r = await client.post("/api/tokens", json={"name": scope, "scope": scope}, headers=person.headers)
    assert r.status_code == 201, r.text
    return r.json()["token"]


async def rpc(client, raw: str | None, method: str, params: dict | None = None):
    headers = {"Accept": ACCEPT, **({"Authorization": f"Bearer {raw}"} if raw else {})}
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return await client.post("/mcp", headers=headers, json=body)


async def call(client, raw: str, name: str, **arguments) -> tuple[bool, object]:
    r = await rpc(client, raw, "tools/call", {"name": name, "arguments": arguments})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    text = result["content"][0]["text"]
    return result.get("isError", False), (text if result.get("isError") else json.loads(text))


async def declare(client, person, **overrides) -> dict:
    body = {"title": "Mailbox rule forwarding finance mail", "severity": "high", "tlp": "amber", **overrides}
    r = await client.post("/api/incidents", json=body, headers=person.headers)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
async def soc(make_person, sign_in):
    return await sign_in(await make_person("soc@example.org", Role.RESPONDER, "SOC", name="Sam Soc"))


async def test_only_a_valid_api_token_gets_in(db, client, soc):
    assert (await rpc(client, None, "tools/list")).status_code == 401
    assert (await rpc(client, soc.access, "tools/list")).status_code == 401  # a browser session's JWT
    assert (await rpc(client, "ost_not-a-real-token", "tools/list")).status_code == 401

    raw = await token(client, soc, "mcp")
    r = await rpc(
        client,
        raw,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["serverInfo"]["name"] == "Ostraka"
    assert "lead is not a confirmed finding" in r.json()["result"]["instructions"]


async def test_read_only_tokens_get_only_the_read_tools(db, client, soc):
    read = await token(client, soc, "mcp")
    full = await token(client, soc, "full")
    names = lambda r: {t["name"] for t in r.json()["result"]["tools"]}  # noqa: E731
    offered = names(await rpc(client, read, "tools/list"))
    assert {"list_records", "get_record", "findings", "tasks"} <= offered
    assert not {"add_comment", "add_lead"} & offered
    assert {"add_comment", "add_lead"} <= names(await rpc(client, full, "tools/list"))

    incident = await declare(client, soc)
    failed, message = await call(client, read, "add_comment", ref=incident["ref"], body="hello")
    assert failed and "No tool named add_comment" in message


async def test_reads_go_through_the_api_as_the_tokens_owner(db, client, soc, make_person, sign_in):
    raw = await token(client, soc, "mcp")
    incident = await declare(client, soc)
    other = await sign_in(await make_person("dee@example.org", Role.RESPONDER, "Forensics", name="Dee"))
    hidden = await declare(client, other, title="Not for the SOC")

    failed, records = await call(client, raw, "list_records")
    assert not failed and [r["ref"] for r in records] == [incident["ref"]]
    assert records[0]["title"] == incident["title"] and records[0]["teams"] == ["SOC"]

    failed, record = await call(client, raw, "get_record", ref=incident["ref"].lower())
    assert not failed and record["ref"] == incident["ref"] and "links" in record
    failed, message = await call(client, raw, "get_record", ref=hidden["ref"])
    assert failed and "No such incident" in message

    failed, found = await call(client, raw, "look_up", ref=f"#{incident['ref']}")
    assert not failed and found["incident_ref"] == incident["ref"]
    failed, me = await call(client, raw, "whoami")
    assert me["name"] == "Sam Soc"


async def test_restricted_records_stay_inside_ostraka(db, client, soc):
    raw = await token(client, soc, "full")
    red = await declare(client, soc, title="Board member's mailbox", tlp="red")
    strict = await declare(client, soc, title="Insider case", tlp="amber+strict")

    failed, records = await call(client, raw, "list_records")
    assert {r["ref"] for r in records} == {red["ref"], strict["ref"]}
    assert all(r["title"].startswith("(TLP:") and "teams" not in r for r in records)
    assert "Board member" not in json.dumps(records)

    for name, extra in (
        ("get_record", {}),
        ("findings", {}),
        ("timeline", {}),
        ("add_comment", {"body": "x"}),
    ):
        failed, message = await call(client, raw, name, ref=red["ref"], **extra)
        assert failed and "keeps it out of MCP clients" in message, name
    failed, message = await call(client, raw, "look_up", ref=red["ref"])
    assert failed and "TLP:RED" in message


async def test_a_full_token_adds_leads_and_comments_but_never_confirms(db, client, soc):
    raw = await token(client, soc, "full")
    incident = await declare(client, soc)
    failed, lead = await call(
        client,
        raw,
        "add_lead",
        ref=incident["ref"],
        kind="evidence",
        title="Inbox rule created from a new IP",
        statement="Seen in the unified audit log.",
        techniques=["T1564.008"],
    )
    assert not failed, lead
    assert lead["status"] == "lead" and lead["created_by"]["name"] == "Sam Soc"
    assert lead["techniques"] == ["T1564.008"]

    failed, comment = await call(client, raw, "add_comment", ref=incident["ref"], body="Lead added from MCP.")
    assert not failed and comment["author"]["name"] == "Sam Soc"
    failed, found = await call(client, raw, "findings", ref=incident["ref"], status="lead")
    assert [f["ref"] for f in found] == [lead["ref"]]

    failed, message = await call(client, raw, "add_lead", ref=incident["ref"], kind="evidence", title="x")
    assert failed and "too short" in message  # the tool's own schema
    failed, message = await call(
        client, raw, "add_lead", ref=incident["ref"], kind="evidence", title="Odd", techniques=["nope"]
    )
    assert failed and message.startswith("techniques")  # the API's validation, reported by field
