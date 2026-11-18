"""Pnyx end to end against Postgres: the bearings from a real record, masked per viewer, and a free
question answered by a stand-in model whose answer is checked before it's shown."""

import pytest

from ostraka.config import get_settings
from ostraka.updates import ai
from tests.test_handover import add_task
from tests.test_incidents import declare, people, team_id  # noqa: F401

pytestmark = pytest.mark.db

IP = "185.220.101.4"


async def test_the_bearings_answer_from_the_record_and_mask_for_observers(db, client, people):  # noqa: F811
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    task = await add_task(
        client, p["soc"], incident, title=f"Block {IP} at the edge", team_id=await team_id(db, "SOC")
    )
    await client.post(
        f"{base}/findings", json={"kind": "evidence", "title": "Beacon to the VPS"}, headers=p["soc"].headers
    )

    r = await client.get(f"{base}/pnyx", headers=p["soc"].headers)
    assert r.status_code == 200, r.text
    answers = {a["key"]: a for a in r.json()["answers"]}
    assert list(answers) == ["now", "outstanding", "last_update", "concern", "leadership"]
    assert answers["now"]["question"] == "What do I do now?"
    assert any(task["ref"] in line and IP in line for line in answers["outstanding"]["lines"])
    assert answers["concern"]["lines"][0].startswith("1 lead still open")
    assert (
        answers["last_update"]["lines"][0]
        == f"No stakeholder update has been sent for {incident['ref']} yet."
    )

    # an observer reads the same bearings with the indicator masked
    seen = (await client.get(f"{base}/pnyx", headers=p["exec"].headers)).json()
    text = " ".join(line for a in seen["answers"] for line in a["lines"])
    assert IP not in text and "[ip address]" in text

    # someone outside the incident gets nothing
    assert (await client.get(f"{base}/pnyx", headers=p["dfir"].headers)).status_code == 404


async def test_a_free_question_is_answered_from_the_masked_record_and_checked(
    db,
    client,
    people,  # noqa: F811
    monkeypatch,
):
    p = await people()
    incident = await declare(client, p["soc"])
    base = f"/api/incidents/{incident['id']}"
    task = await add_task(
        client, p["soc"], incident, title=f"Block {IP} at the edge", team_id=await team_id(db, "SOC")
    )
    monkeypatch.setattr(ai, "provider_for", lambda incident: "ollama")
    seen: list[str] = []

    async def answers(provider, prompt):
        seen.append(prompt)
        return f"Blocking the address is {task['ref']}, still open."

    monkeypatch.setattr(ai, "complete", answers)
    r = await client.post(
        f"{base}/pnyx/ask", json={"question": f"Who is blocking {IP}?"}, headers=p["soc"].headers
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"answer": f"Blocking the address is {task['ref']}, still open.", "provider": "ollama"}
    # the model never saw the indicator, in the record or the question
    assert IP not in seen[0] and task["ref"] in seen[0]

    async def invents(provider, prompt):
        return "TSK-999 covers it."

    monkeypatch.setattr(ai, "complete", invents)
    r = await client.post(f"{base}/pnyx/ask", json={"question": "Who covers it?"}, headers=p["soc"].headers)
    assert r.status_code == 422
    assert "TSK-999" in r.json()["detail"] and "doesn't hold" in r.json()["detail"]


async def test_the_tlp_guard_keeps_a_red_incident_off_a_hosted_model(db, client, people, monkeypatch):  # noqa: F811
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(settings, "anthropic_model", "a-model")
    monkeypatch.setattr(settings, "ollama_model", "")
    p = await people()
    incident = await declare(client, p["soc"], tlp="red")
    base = f"/api/incidents/{incident['id']}"

    bearings = (await client.get(f"{base}/pnyx", headers=p["soc"].headers)).json()
    assert bearings["can_ask"] is False and "only a local Ollama model" in bearings["why_not"]
    # the bearings still answer: they aren't AI
    assert len(bearings["answers"]) == 5

    async def must_not_run(provider, prompt):
        raise AssertionError("the model was called")

    monkeypatch.setattr(ai, "complete", must_not_run)
    r = await client.post(f"{base}/pnyx/ask", json={"question": "What's next?"}, headers=p["soc"].headers)
    assert r.status_code == 409 and "only a local Ollama model" in r.json()["detail"]
