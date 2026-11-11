"""STIX, MISP and Navigator exports against Postgres: what an incident exports as, who may export
it, the TLP:RED rule, and pushing to MISP only on an explicit request, on an edition with automation."""

import json

import pytest
from sqlalchemy import select, text

from ostraka.exports import misp
from ostraka.models import ActivityEvent, MispSettings, SecurityEvent
from tests.test_exports_units import check_bundle
from tests.test_incidents import declare, people  # noqa: F401  (the fixture)
from tests.test_webhooks import install

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
async def no_misp(db):
    await db.execute(text("DELETE FROM misp_settings"))
    await db.commit()


@pytest.fixture
def misp_server(monkeypatch):
    """A MISP instance in memory: what was asked of it, and the events it holds."""
    server = {"calls": [], "events": {}}

    async def fetch(method, url, *, key, verify, body=None):
        server["calls"].append((method, url, key, verify, json.loads(body) if body else None))
        if url.endswith("/servers/getVersion"):
            return 200, b'{"version": "2.5.2"}'
        if method == "GET":
            return (200 if url.rsplit("/", 1)[1] in server["events"] else 404), b"{}"
        event = json.loads(body)["Event"]
        server["events"][event["uuid"]] = event
        return 200, json.dumps({"Event": {"id": "77", "uuid": event["uuid"]}}).encode()

    monkeypatch.setattr(misp, "fetch", fetch)
    return server


async def worked(client, p, **overrides) -> dict:
    """An incident Sam declared, with a confirmed finding citing a malicious IP and a lead citing a URL."""
    incident = await declare(client, p["soc"], title="Beacon from FIN-WS-07 to 203.0.113.45", **overrides)
    base = f"/api/incidents/{incident['id']}"
    h = p["soc"].headers
    ip = (
        await client.post(
            f"{base}/observables",
            json={"value": "203.0.113.45", "verdict": "malicious", "sighting": {"host": "FIN-WS-07"}},
            headers=h,
        )
    ).json()
    url = (
        await client.post(
            f"{base}/observables",
            json={"value": "hxxps://evil[.]example/gate.php", "verdict": "suspicious"},
            headers=h,
        )
    ).json()
    await client.post(f"{base}/observables", json={"value": "example.com", "verdict": "benign"}, headers=h)
    confirmed = (
        await client.post(
            f"{base}/findings",
            json={
                "kind": "evidence",
                "title": "Beaconing to 203.0.113.45",
                "techniques": ["T1071.001"],
                "observable_ids": [ip["id"]],
            },
            headers=h,
        )
    ).json()
    r = await client.post(
        f"{base}/findings/{confirmed['id']}/verifications",
        json={
            "method": "search",
            "platform": "Defender XDR",
            "scope": "FIN-WS-07",
            "query": "DeviceNetworkEvents",
        },
        headers=p["lead"].headers,
    )
    assert r.status_code == 201, r.text
    r = await client.post(f"{base}/findings/{confirmed['id']}/confirm", json={"version": 1}, headers=h)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"{base}/findings",
        json={
            "kind": "potential",
            "title": "Exfiltration",
            "techniques": ["T1041"],
            "observable_ids": [url["id"]],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return incident


async def test_an_incident_exports_as_stix_misp_and_a_layer(db, client, people):  # noqa: F811
    p = await people()
    incident = await worked(client, p)
    base = f"/api/incidents/{incident['id']}/export"

    r = await client.get(f"{base}/stix", headers=p["soc"].headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/stix+json")
    assert r.headers["content-disposition"] == f'attachment; filename="{incident["ref"]}-stix.json"'
    objects = check_bundle(r.json())
    indicators = {o["pattern"]: o for o in objects.values() if o["type"] == "indicator"}
    assert indicators["[ipv4-addr:value = '203.0.113.45']"]["labels"] == ["confirmed"]
    assert indicators["[url:value = 'https://evil.example/gate.php']"]["labels"] == ["lead"]
    assert not any("example.com" in p for p in indicators)  # benign: left out
    report = next(o for o in objects.values() if o["type"] == "report")
    assert "203.0.113.45" not in report["name"]  # prose is masked; the value travels in its indicator
    assert [o for o in objects.values() if o["type"] == "sighting"][0][
        "description"
    ] == "Seen: host FIN-WS-07."

    event = (await client.get(f"{base}/misp", headers=p["soc"].headers)).json()["Event"]
    ids = {a["value"]: a["to_ids"] for a in event["Attribute"] if a["type"] in ("ip-dst", "url")}
    assert ids == {"203.0.113.45": True, "https://evil.example/gate.php": False}
    assert {"name": "tlp:amber"} in event["Tag"]

    layer = (await client.get(f"{base}/navigator", headers=p["soc"].headers)).json()
    assert [t["techniqueID"] for t in layer["techniques"]] == ["T1071.001"]
    layer = (await client.get(f"{base}/navigator", params={"leads": "true"}, headers=p["soc"].headers)).json()
    assert [t["techniqueID"] for t in layer["techniques"]] == ["T1041", "T1071.001"]

    audit = (await db.scalars(select(SecurityEvent).where(SecurityEvent.action == "intel_exported"))).all()
    assert [e.detail["format"] for e in audit] == ["stix", "misp", "navigator", "navigator"]
    logged = (await db.scalars(select(ActivityEvent).where(ActivityEvent.action == "intel_exported"))).all()
    assert [e.summary for e in logged] == [
        "Sam Soc exported a STIX 2.1 bundle",
        "Sam Soc exported a MISP event",
        "Sam Soc exported an ATT&CK Navigator layer",
        "Sam Soc exported an ATT&CK Navigator layer with leads",
    ]


async def test_exports_follow_the_report_rule_and_tlp_red_is_the_commanders_alone(db, client, people):  # noqa: F811
    p = await people()
    amber = await declare(client, p["soc"])
    red = await declare(client, p["soc"], tlp="red")
    for incident in (amber, red):  # Lee works on both
        r = await client.post(
            f"/api/incidents/{incident['id']}/observables",
            json={"value": "198.51.100.7"},
            headers=p["lead"].headers,
        )
        assert r.status_code == 201, r.text

    for who in ("exec", "admin"):  # an observer; an admin who never touched it
        assert (
            await client.get(f"/api/incidents/{amber['id']}/export/stix", headers=p[who].headers)
        ).status_code == 403
    assert (
        await client.get(f"/api/incidents/{amber['id']}/export/stix", headers=p["dfir"].headers)
    ).status_code == 404
    assert (
        await client.get(f"/api/incidents/{amber['id']}/export/stix", headers=p["lead"].headers)
    ).status_code == 200

    r = await client.get(f"/api/incidents/{red['id']}/export/misp", headers=p["lead"].headers)
    assert r.status_code == 403 and "commander only" in r.json()["detail"]
    r = await client.get(f"/api/incidents/{red['id']}/export/stix", headers=p["soc"].headers)
    assert r.status_code == 200
    assert {"marking-definition--e828b379-4e03-4974-9ac4-e53a884c97c1"} == {
        o["id"] for o in r.json()["objects"] if o["type"] == "marking-definition"
    }
    offered = (await client.get(f"/api/incidents/{red['id']}/exports", headers=p["soc"].headers)).json()
    assert offered["downloads"] is True and offered["misp"]["available"] is False
    assert "never sent" in offered["misp"]["reason"]


async def test_misp_settings_are_admin_only_and_the_key_is_kept_encrypted(db, client, people):  # noqa: F811
    p = await people()
    body = {"url": "https://misp.example.org/", "api_key": "s3cret-misp-key", "verify_tls": True}
    assert (await client.put("/api/misp", json=body, headers=p["lead"].headers)).status_code == 403
    r = await client.put("/api/misp", json=body, headers=p["admin"].headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["configured"] and out["has_key"] and out["url"] == "https://misp.example.org"
    assert "s3cret" not in r.text and out["sending"] is False
    stored = await db.scalar(select(MispSettings))
    assert stored.api_key != "s3cret-misp-key" and "s3cret" not in stored.api_key

    bad = {**body, "url": "http://misp.example.org"}
    assert (
        await client.put("/api/misp", json={**bad, "version": 1}, headers=p["admin"].headers)
    ).status_code == 422
    stale = await client.put(
        "/api/misp", json={**body, "api_key": None, "version": 7}, headers=p["admin"].headers
    )
    assert stale.status_code == 409
    kept = await client.put(
        "/api/misp",
        json={**body, "api_key": None, "verify_tls": False, "version": 1},
        headers=p["admin"].headers,
    )
    assert kept.status_code == 200 and kept.json()["verify_tls"] is False and kept.json()["has_key"]
    assert (await client.post("/api/misp/check", headers=p["admin"].headers)).status_code == 402  # Free
    assert (await client.delete("/api/misp", headers=p["admin"].headers)).status_code == 204
    actions = (
        await db.scalars(select(SecurityEvent.action).where(SecurityEvent.action.like("misp_%")))
    ).all()
    assert actions == ["misp_configured", "misp_configured", "misp_removed"]


async def test_pushing_to_misp_needs_automation_and_never_happens_on_its_own(
    db,
    client,
    people,  # noqa: F811
    licence_line,
    misp_server,
):
    p = await people()
    await client.put(
        "/api/misp", json={"url": "https://misp.example.org", "api_key": "k3y"}, headers=p["admin"].headers
    )
    incident = await worked(client, p)
    push = f"/api/incidents/{incident['id']}/misp"
    assert misp_server["calls"] == []  # nothing pushed while the incident was worked

    r = await client.post(push, headers=p["soc"].headers)
    assert r.status_code == 402 and "Pro and Services" in r.json()["detail"]
    offered = (await client.get(f"/api/incidents/{incident['id']}/exports", headers=p["soc"].headers)).json()
    assert offered["misp"] == {"available": False, "reason": offered["misp"]["reason"]}

    await install(client, p["admin"], licence_line)
    assert (await client.post("/api/misp/check", headers=p["admin"].headers)).json() == {
        "ok": True,
        "version": "2.5.2",
        "error": "",
    }
    offered = (await client.get(f"/api/incidents/{incident['id']}/exports", headers=p["soc"].headers)).json()
    assert offered["misp"] == {"available": True, "reason": None}
    first = await client.post(push, headers=p["soc"].headers)
    assert first.status_code == 200, first.text
    again = await client.post(push, headers=p["soc"].headers)
    assert (first.json()["action"], again.json()["action"]) == ("added", "updated")
    assert first.json()["url"] == "https://misp.example.org/events/view/77"
    (event,) = misp_server["events"].values()
    assert event["published"] is False and "203.0.113.45" not in event["info"]
    assert all(c[2] == "k3y" and c[3] is True for c in misp_server["calls"])
    assert (await client.post(push, headers=p["exec"].headers)).status_code == 403

    logged = (
        await db.scalars(select(ActivityEvent.summary).where(ActivityEvent.action == "misp_pushed"))
    ).all()
    assert logged == ["Sam Soc sent the incident to MISP", "Sam Soc updated the incident in MISP"]
    pushed = (await db.scalars(select(SecurityEvent).where(SecurityEvent.action == "misp_pushed"))).all()
    assert [e.detail["outcome"] for e in pushed] == ["added", "updated"]


async def test_a_restricted_incident_is_never_pushed(db, client, people, licence_line, misp_server):  # noqa: F811
    p = await people()
    await install(client, p["admin"], licence_line)
    await client.put(
        "/api/misp", json={"url": "https://misp.example.org", "api_key": "k3y"}, headers=p["admin"].headers
    )
    for tlp in ("red", "amber+strict"):
        incident = await declare(client, p["soc"], tlp=tlp)
        r = await client.post(f"/api/incidents/{incident['id']}/misp", headers=p["soc"].headers)
        assert r.status_code == 403 and "never sent" in r.json()["detail"]
    assert misp_server["calls"] == []
