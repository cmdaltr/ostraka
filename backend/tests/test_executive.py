"""The executive summary against Postgres: Ostraka's own draft from a real record, a stand-in model's
wording checked and set aside when it fails, saving under the same rules, masking, and reports."""

import pytest
from sqlalchemy import select

from ostraka.models import Incident, User
from ostraka.reports import incident as content
from ostraka.updates import ai
from tests.test_analysis import finding
from tests.test_incidents import declare, people  # noqa: F401  (the fixture)

pytestmark = pytest.mark.db

IP = "185.220.101.4"


async def record(client, p) -> tuple[dict, dict, dict, str]:
    incident = await declare(client, p["soc"], summary=f"Finance mail forwarded out; beacons to {IP}.")
    base = f"/api/incidents/{incident['id']}"
    rule = await finding(client, p["soc"], incident["id"], "Inbox rule created by the attacker", confirm=True)
    lead = await finding(client, p["soc"], incident["id"], f"Beacon to {IP}")
    r = await client.post(
        f"{base}/comments",
        json={"body": f"I think {lead['ref']} is the exfil route"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 201, r.text
    return incident, rule, lead, base


async def test_ostraka_drafts_four_sentences_and_a_person_saves_them(db, client, people):  # noqa: F811
    p = await people()
    incident, rule, lead, base = await record(client, p)

    r = await client.post(f"{base}/executive-summary/draft", json={}, headers=p["soc"].headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["worded"] == "ostraka" and out["can_ask"] is False and "switched off" in out["why_not"]
    s = out["sentences"]
    assert len(s) == 4
    assert s[0].startswith(f"{incident['ref']} {incident['codename']} is a high-severity incident")
    assert s[1] == f"1 finding has been confirmed: {rule['ref']} (Inbox rule created by the attacker)."
    assert "1 lead remains to be confirmed or refuted" in s[2]
    assert s[3] == "No assessment has been made yet, so this carries no judgement of what it means."

    # an observer reads it but can't draft or save one
    assert (
        await client.post(f"{base}/executive-summary/draft", json={}, headers=p["exec"].headers)
    ).status_code == 403

    version = (await client.get(base, headers=p["soc"].headers)).json()["version"]
    r = await client.put(
        f"{base}/executive-summary",
        json={"version": version, "text": out["text"], "worded": "ostraka"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text
    saved = r.json()["executive"]
    assert saved["text"] == out["text"] and saved["worded"] == "ostraka" and saved["by"]["name"] == "Sam Soc"

    # a person's wording is held to the same rules: a lead isn't confirmed
    r = await client.put(
        f"{base}/executive-summary",
        json={
            "version": r.json()["version"],
            "text": f"{incident['ref']} is an incident. {lead['ref']} confirmed the exfiltration.",
        },
        headers=p["soc"].headers,
    )
    assert r.status_code == 422 and "it is still a lead" in r.json()["detail"]

    # a stale version is refused
    r = await client.put(
        f"{base}/executive-summary",
        json={"version": 1, "text": "", "worded": "person"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 409


async def test_a_models_wording_is_used_when_it_passes_and_set_aside_when_not(
    db,
    client,
    people,  # noqa: F811
    monkeypatch,
):
    p = await people()
    incident, rule, lead, base = await record(client, p)
    monkeypatch.setattr(ai, "provider_for", lambda incident: "ollama")
    prompts: list[str] = []
    wording = [
        f"{incident['ref']} {incident['codename']} is an open high-severity incident about forwarded mail.",
        f"The attacker created an inbox rule, confirmed as {rule['ref']}.",
        f"A beacon to {IP} is still a lead, {lead['ref']}.",
        "No judgement has been made yet.",
    ]

    async def writes(provider, prompt):
        prompts.append(prompt)
        return str(wording).replace("'", '"')

    monkeypatch.setattr(ai, "complete", writes)
    r = await client.post(f"{base}/executive-summary/draft", json={}, headers=p["soc"].headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["worded"] == "ollama" and out["set_aside"] == ""
    # the model read the summary and the discussion, masked; what comes back is masked too
    assert "Finance mail forwarded out" in prompts[0] and "is the exfil route" in prompts[0]
    assert IP not in prompts[0] and IP not in out["text"] and "[ip address]" in out["text"]

    version = (await client.get(base, headers=p["soc"].headers)).json()["version"]
    r = await client.put(
        f"{base}/executive-summary",
        json={"version": version, "text": out["text"], "worded": "ollama"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200 and r.json()["executive"]["worded"] == "ollama"

    async def overreaches(provider, prompt):
        return str([*wording[:2], f"{lead['ref']} confirmed the exfiltration.", wording[3]]).replace("'", '"')

    monkeypatch.setattr(ai, "complete", overreaches)
    out = (await client.post(f"{base}/executive-summary/draft", json={}, headers=p["soc"].headers)).json()
    assert out["worded"] == "ostraka" and "it is still a lead" in out["set_aside"]
    assert out["sentences"][1].startswith("1 finding has been confirmed")

    # asked for Ostraka's own, the model isn't called
    async def must_not_run(provider, prompt):
        raise AssertionError("the model was called")

    monkeypatch.setattr(ai, "complete", must_not_run)
    out = (
        await client.post(f"{base}/executive-summary/draft", json={"ai": False}, headers=p["soc"].headers)
    ).json()
    assert out["worded"] == "ostraka" and out["can_ask"] is True


async def test_reports_open_with_it_and_the_briefing_masks_it(db, client, people):  # noqa: F811
    p = await people()
    incident, rule, lead, base = await record(client, p)
    version = (await client.get(base, headers=p["soc"].headers)).json()["version"]
    text = (
        f"{incident['ref']} is an incident. {rule['ref']} is confirmed. {lead['ref']} is a lead. "
        "No judgement yet."
    )
    r = await client.put(
        f"{base}/executive-summary",
        json={"version": version, "text": text, "worded": "person"},
        headers=p["soc"].headers,
    )
    assert r.status_code == 200, r.text
    seen = (await client.get(base, headers=p["exec"].headers)).json()["executive"]
    assert seen["text"] == text and seen["worded"] == "person"

    row = await db.scalar(select(Incident).where(Incident.id == incident["id"]))
    await db.refresh(row)
    viewer = await db.scalar(select(User).where(User.name == "Sam Soc"))
    gathered = await content.gather(db, row, viewer)
    for build in (content.technical, content.briefing):
        _, blocks = build(gathered)
        assert blocks[0] == ("section", "Executive Summary") and blocks[1] == ("para", text)
