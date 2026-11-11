"""Phase 3 end to end against Postgres: tasks and queues, checklists, nudges, the reminder and
escalation scheduler, playbooks, notifications and every way they leave ostraka."""

from datetime import timedelta

import pytest
from sqlalchemy import select, text

from ostraka import scheduler
from ostraka.config import get_settings
from ostraka.db import sessions
from ostraka.models import PushSubscription, Role, Team, utcnow
from tests.test_incidents import declare, team_id

pytestmark = pytest.mark.db

HOOK = "https://prod-01.westeurope.logic.azure.com/workflows/abc/triggers/manual/paths/invoke?sig=secret"


@pytest.fixture
def people(make_person, sign_in):
    """SOC (a lead, two responders, an observer), a DFIR lead and responder, and an admin."""

    async def build():
        cast = {
            "admin": await make_person("admin@example.org", Role.ADMIN, None, name="Ada Admin"),
            "lead": await make_person("lead@example.org", Role.TEAM_LEAD, "SOC", name="Lee Lead"),
            "soc": await make_person("soc@example.org", Role.RESPONDER, "SOC", name="Sam Soc"),
            "soc2": await make_person("soc2@example.org", Role.RESPONDER, "SOC", name="Sky Soc"),
            "exec": await make_person("exec@example.org", Role.OBSERVER, "SOC", name="Eve Exec"),
            "dlead": await make_person("dlead@example.org", Role.TEAM_LEAD, "Forensics", name="Dana Lead"),
            "dfir": await make_person("dfir@example.org", Role.RESPONDER, "Forensics", name="Dee Dfir"),
        }
        for person in cast.values():
            await sign_in(person)
        return cast

    return build


async def notes(client, person) -> dict:
    r = await client.get("/api/notifications", headers=person.headers)
    assert r.status_code == 200, r.text
    return r.json()


async def add_task(client, person, incident, **body) -> dict:
    r = await client.post(
        f"/api/incidents/{incident['id']}/tasks",
        json={"title": "Pull the mailbox audit log", **body},
        headers=person.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def connect_channel(client, db, person, team="SOC"):
    r = await client.put(
        f"/api/teams/{await team_id(db, team)}/channel", json={"url": HOOK}, headers=person.headers
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---- tasks and queues -----------------------------------------------------------------------------


async def test_a_team_task_waits_in_the_queue_until_someone_in_the_team_claims_it(
    db, client, people, outbox, settle
):
    p = await people()
    await connect_channel(client, db, p["lead"])
    incident = await declare(client, p["soc"])
    task = await add_task(
        client, p["soc"], incident, team_id=await team_id(db, "SOC"), checklist=["Export", "Hash"]
    )
    assert task["ref"].startswith("TSK-") and task["assignee"] is None and task["team"]["name"] == "SOC"
    assert [i["text"] for i in task["checklist"]] == ["Export", "Hash"]
    await settle()

    # The queue hears about it: the team's other members in ostraka, and its Teams channel
    assert (await notes(client, p["soc2"]))["items"][0]["kind"] == "team_task"
    assert (await notes(client, p["soc"]))["unread"] == 0  # not told about your own action
    assert (await notes(client, p["exec"]))["unread"] == 0  # observers don't work queues
    card = outbox.teams()[0]["attachments"][0]["content"]
    assert task["ref"] in card["body"][0]["text"]
    assert card["actions"][0]["url"].endswith(f"/incidents/{incident['ref']}?s=tasks#{task['ref']}")

    queue = (await client.get("/api/tasks/mine", headers=p["soc2"].headers)).json()
    assert [(t["ref"], t["queued"]) for t in queue] == [(task["ref"], True)]

    base = f"/api/incidents/{incident['id']}/tasks/{task['id']}"
    await client.post(
        f"/api/incidents/{incident['id']}/teams",
        json={"team_id": await team_id(db, "Forensics")},
        headers=p["soc"].headers,
    )
    assert (await client.post(f"{base}/claim", headers=p["dfir"].headers)).status_code == 403
    claimed = (await client.post(f"{base}/claim", headers=p["soc2"].headers)).json()
    assert claimed["assignee"]["name"] == "Sky Soc" and claimed["status"] == "in_progress"
    assert (await client.post(f"{base}/claim", headers=p["soc"].headers)).status_code == 409
    mine = (await client.get("/api/tasks/mine", headers=p["soc2"].headers)).json()
    assert [(t["ref"], t["queued"]) for t in mine] == [(task["ref"], False)]

    released = (await client.post(f"{base}/release", headers=p["soc2"].headers)).json()
    assert released["assignee"] is None and released["status"] == "open"


async def test_giving_a_task_to_someone_outside_the_incident_needs_management_rights(
    db, client, people, settle, outbox
):
    p = await people()
    incident = await declare(client, p["soc"])  # Sam is commander
    await client.put(
        f"/api/incidents/{incident['id']}/members/{p['soc2'].id}", json={}, headers=p["soc"].headers
    )
    dfir = str(p["dfir"].id)
    r = await client.post(
        f"/api/incidents/{incident['id']}/tasks",
        json={"title": "Image the laptop", "assignee_id": dfir},
        headers=p["soc2"].headers,
    )
    assert r.status_code == 403  # a responder can't bring another team's person in
    task = await add_task(client, p["soc"], incident, title="Image the laptop", assignee_id=dfir)
    assert task["assignee"]["name"] == "Dee Dfir"
    detail = (await client.get(f"/api/incidents/{incident['id']}", headers=p["soc"].headers)).json()
    assert "Dee Dfir" in [m["user"]["name"] for m in detail["members"]]
    await settle()
    assert (await notes(client, p["dfir"]))["items"][0]["kind"] == "assigned"

    r = await client.post(
        f"/api/incidents/{incident['id']}/tasks",
        json={"title": "Brief the board", "assignee_id": str(p["exec"].id)},
        headers=p["soc"].headers,
    )
    assert r.status_code == 422  # observers don't hold tasks
    r = await client.post(
        f"/api/incidents/{incident['id']}/tasks", json={"title": "Nobody's task"}, headers=p["soc"].headers
    )
    assert r.status_code == 422


async def test_task_edits_carry_a_version_and_blocking_needs_a_reason(db, client, people, outbox):
    p = await people()
    incident = await declare(client, p["soc"])
    task = await add_task(client, p["soc"], incident, assignee_id=str(p["soc"].id))
    url = f"/api/incidents/{incident['id']}/tasks/{task['id']}"
    r = await client.patch(url, json={"version": 1, "status": "blocked"}, headers=p["soc"].headers)
    assert r.status_code == 422
    r = await client.patch(
        url,
        json={"version": 1, "status": "blocked", "blocked_reason": "Waiting on legal"},
        headers=p["soc"].headers,
    )
    assert r.json()["status"] == "blocked" and r.json()["version"] == 2
    assert (
        await client.patch(url, json={"version": 1, "title": "Stale"}, headers=p["soc"].headers)
    ).status_code == 409
    done = (await client.patch(url, json={"version": 2, "status": "done"}, headers=p["soc"].headers)).json()
    assert done["completed_by"]["name"] == "Sam Soc" and done["blocked_reason"] == ""
    reopened = (
        await client.patch(url, json={"version": 3, "status": "open"}, headers=p["soc"].headers)
    ).json()
    assert reopened["completed_at"] is None
    detail = (await client.get(f"/api/incidents/{incident['id']}", headers=p["soc"].headers)).json()
    assert detail["counts"]["tasks"] == 1


async def test_checklist_items_are_ticked_independently(db, client, people, outbox):
    p = await people()
    incident = await declare(client, p["soc"])
    task = await add_task(client, p["soc"], incident, assignee_id=str(p["soc"].id), checklist=["Export"])
    base = f"/api/incidents/{incident['id']}/tasks/{task['id']}/checklist"
    added = (await client.post(base, json={"text": "Hash the export"}, headers=p["soc"].headers)).json()
    first, second = added["checklist"]
    ticked = (
        await client.patch(f"{base}/{first['id']}", json={"done": True}, headers=p["soc"].headers)
    ).json()
    assert ticked["checklist"][0]["done"] and ticked["checklist"][0]["done_by"]["name"] == "Sam Soc"
    left = (await client.delete(f"{base}/{second['id']}", headers=p["soc"].headers)).json()
    assert [i["text"] for i in left["checklist"]] == ["Export"]


async def test_a_nudge_reaches_the_owner_once_per_quarter_hour(db, client, people, outbox, settle):
    p = await people()
    incident = await declare(client, p["soc"])
    await client.put(
        f"/api/incidents/{incident['id']}/members/{p['soc2'].id}", json={}, headers=p["soc"].headers
    )
    task = await add_task(client, p["soc"], incident, assignee_id=str(p["soc2"].id))
    url = f"/api/incidents/{incident['id']}/tasks/{task['id']}/nudge"
    assert (
        await client.post(url, json={"message": "Any news?"}, headers=p["soc"].headers)
    ).status_code == 204
    assert (await client.post(url, json={}, headers=p["soc"].headers)).status_code == 429
    assert (await client.post(url, json={}, headers=p["soc2"].headers)).status_code == 422  # only yourself
    await settle()
    nudge = (await notes(client, p["soc2"]))["items"][0]
    assert nudge["kind"] == "nudge" and nudge["body"] == "Any news?" and nudge["actor"]["name"] == "Sam Soc"


# ---- the scheduler --------------------------------------------------------------------------------


async def tick(at):
    async with sessions()() as session:
        return await scheduler.tick(session, now=at)


async def kinds(client, person) -> list[str]:
    # The same ticks fall due for stakeholder updates (tests/test_updates.py); these tests are about tasks
    return [n["kind"] for n in (await notes(client, person))["items"] if n["kind"] != "update_due"]


async def test_reminders_then_escalation_to_the_lead_and_the_commander(db, client, people, outbox, settle):
    p = await people()
    incident = await declare(client, p["lead"])  # Lee is commander and SOC lead
    await client.put(
        f"/api/incidents/{incident['id']}/members/{p['admin'].id}",
        json={"role": "commander"},
        headers=p["lead"].headers,
    )  # Ada takes command; Lee stays as SOC's lead
    due = utcnow() + timedelta(minutes=30)
    task = await add_task(client, p["lead"], incident, assignee_id=str(p["soc"].id), due_at=due.isoformat())
    await settle()
    assert await kinds(client, p["soc"]) == ["assigned"]

    assert await tick(due - timedelta(minutes=30)) == 1
    assert await tick(due - timedelta(minutes=20)) == 0  # the due-soon reminder goes once
    assert await tick(due + timedelta(minutes=1)) == 1
    assert await tick(due + timedelta(hours=2)) == 0  # not again until the repeat interval
    await settle()
    assert await kinds(client, p["soc"]) == ["overdue", "due_soon", "assigned"]
    assert await kinds(client, p["lead"]) == []

    assert await tick(due + timedelta(hours=4, minutes=1)) == 2  # the repeat, and the team lead
    assert await tick(due + timedelta(hours=24, minutes=1)) == 2  # the repeat, and the commander
    await settle()
    assert await kinds(client, p["lead"]) == ["escalated"]
    assert await kinds(client, p["admin"]) == ["escalated", "commander"]
    activity = (
        await client.get(f"/api/incidents/{incident['id']}/activity", headers=p["admin"].headers)
    ).json()
    escalations = [e["summary"] for e in activity if e["action"] == "task_escalated"]
    assert escalations == [
        f"Ostraka escalated {task['ref']} to the team lead (Lee Lead): 4 h overdue",
        f"Ostraka escalated {task['ref']} to the incident commander (Ada Admin): 24 h overdue",
    ]

    # Finishing the task stops the reminders; so does a closed incident for the rest
    url = f"/api/incidents/{incident['id']}/tasks/{task['id']}"
    version = (await client.get(f"/api/incidents/{incident['id']}/tasks", headers=p["soc"].headers)).json()[
        0
    ]["version"]
    await client.patch(url, json={"version": version, "status": "done"}, headers=p["soc"].headers)
    assert await tick(due + timedelta(hours=40)) == 0


async def test_an_unclaimed_team_task_reminds_its_queue_and_channel(db, client, people, outbox, settle):
    p = await people()
    await connect_channel(client, db, p["lead"])
    incident = await declare(client, p["soc"])
    due = utcnow() - timedelta(minutes=5)
    await add_task(client, p["soc"], incident, team_id=await team_id(db, "SOC"), due_at=due.isoformat())
    await settle()
    before = len(outbox.teams())
    assert await tick(utcnow()) == 1
    await settle()
    assert "overdue" in outbox.teams()[before]["attachments"][0]["content"]["body"][0]["text"]
    assert "overdue" in await kinds(client, p["soc2"])
    assert "overdue" in await kinds(client, p["soc"])  # the scheduler isn't anyone's own action


async def test_only_one_process_runs_a_tick(db, client, people, outbox):
    p = await people()
    incident = await declare(client, p["soc"])
    await add_task(client, p["soc"], incident, assignee_id=str(p["soc"].id), due_at=utcnow().isoformat())
    async with sessions()() as holder:
        await holder.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": scheduler.LOCK_ID})
        assert await tick(utcnow()) == 0
    assert await tick(utcnow()) == 1


# ---- what leaves ostraka ----------------------------------------------------------------------------


async def test_restricted_incidents_send_only_the_reference_outside_ostraka(
    db, client, people, outbox, settle, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "mail.example.org")
    monkeypatch.setattr(settings, "smtp_from", "ostraka@example.org")
    p = await people()
    await connect_channel(client, db, p["lead"])
    red = await declare(client, p["soc"], title="Board member's account taken over", tlp="red")
    await add_task(
        client, p["soc"], red, title="Reset the board member's MFA", team_id=await team_id(db, "SOC")
    )
    await add_task(client, p["soc"], red, title="Call 203.0.113.9's ISP", assignee_id=str(p["soc"].id))
    amber = await declare(client, p["soc"], title="Beaconing to 203.0.113.9")
    await client.put(
        f"/api/incidents/{amber['id']}/members/{p['soc2'].id}", json={}, headers=p["soc"].headers
    )
    await add_task(
        client, p["soc"], amber, title="Block 203.0.113.9 at the edge", assignee_id=str(p["soc2"].id)
    )
    await settle()

    red_card = outbox.teams()[0]["attachments"][0]["content"]
    shown = " ".join(b["text"] for b in red_card["body"])
    assert red["ref"] in shown and "board" not in shown.lower() and "MFA" not in shown
    # In ostraka itself the full text is there for the people who may see it
    assert "Reset the board member's MFA" in (await notes(client, p["soc2"]))["items"][-1]["title"]

    emails = {m["To"]: m for m in outbox.emails}
    sky = emails["soc2@example.org"]
    assert "203.0.113.9" not in sky["Subject"] and "[ip address]" in sky["Subject"]
    assert f"/incidents/{amber['ref']}?s=tasks" in sky.get_content()


async def test_push_is_encrypted_for_the_browser_and_dead_subscriptions_are_dropped(
    db, client, people, outbox, settle
):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from ostraka.notify.push import b64url

    p = await people()
    browser = ec.generate_private_key(ec.SECP256R1())
    p256dh = b64url(
        browser.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    endpoint = "https://fcm.googleapis.com/fcm/send/abc123"
    sub = {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": b64url(b"0123456789abcdef")}}
    assert (
        await client.post("/api/notifications/push-subscriptions", json=sub, headers=p["soc2"].headers)
    ).status_code == 204
    assert (
        (await client.get("/api/notifications/push-key", headers=p["soc2"].headers))
        .json()["key"]
        .startswith("B")
    )

    incident = await declare(client, p["soc"])
    await client.put(
        f"/api/incidents/{incident['id']}/members/{p['soc2'].id}", json={}, headers=p["soc"].headers
    )
    await add_task(client, p["soc"], incident, assignee_id=str(p["soc2"].id))
    await settle()
    url, body, headers = outbox.pushes()[-1]
    assert url == endpoint and headers["Content-Encoding"] == "aes128gcm"
    assert headers["Authorization"].startswith("vapid t=") and b"Pull the mailbox" not in body

    outbox.status = 410
    await add_task(client, p["soc"], incident, title="Second task", assignee_id=str(p["soc2"].id))
    await settle()
    assert await db.scalar(select(PushSubscription.id).where(PushSubscription.endpoint == endpoint)) is None


async def test_team_channels_take_only_workflow_webhooks_and_never_show_the_url(db, client, people, outbox):
    p = await people()
    soc = await team_id(db, "SOC")
    bad = await client.put(
        f"/api/teams/{soc}/channel", json={"url": "http://10.0.0.5/hook"}, headers=p["lead"].headers
    )
    assert bad.status_code == 422
    assert (
        await client.put(f"/api/teams/{soc}/channel", json={"url": HOOK}, headers=p["dlead"].headers)
    ).status_code == 403
    team = await connect_channel(client, db, p["lead"])
    assert team["has_channel"] is True and "sig=secret" not in str(team)
    stored = await db.scalar(select(Team.teams_webhook).where(Team.name == "SOC"))
    assert "sig=secret" not in stored  # encrypted at rest
    assert (await client.post(f"/api/teams/{soc}/channel/test", headers=p["lead"].headers)).status_code == 204
    assert "connected to SOC" in outbox.teams()[-1]["attachments"][0]["content"]["body"][0]["text"]
    listed = (await client.get("/api/teams", headers=p["soc"].headers)).json()
    assert next(t for t in listed if t["name"] == "SOC")["has_channel"] is True


# ---- mentions, replies, handover ------------------------------------------------------------------


async def test_mentions_replies_and_handover_notify_the_right_people(db, client, people, outbox, settle):
    p = await people()
    incident = await declare(client, p["soc"])
    comments = f"/api/incidents/{incident['id']}/comments"
    root = (await client.post(comments, json={"body": "Starting triage"}, headers=p["soc"].headers)).json()
    body = f"@[Lee Lead](user:{p['lead'].id}) and @[SOC](team:{await team_id(db, 'SOC')}) see 203.0.113.9"
    await client.post(comments, json={"body": body, "parent_id": root["id"]}, headers=p["soc2"].headers)
    await client.put(
        f"/api/incidents/{incident['id']}/members/{p['lead'].id}",
        json={"role": "commander"},
        headers=p["soc"].headers,
    )
    await settle()

    assert await kinds(client, p["lead"]) == ["commander", "mention"]
    mention = (await notes(client, p["lead"]))["items"][1]
    assert mention["body"] == "@Lee Lead and @SOC see 203.0.113.9"  # names, not mention markup
    assert "mention" in await kinds(client, p["soc"])  # via the team, so no separate reply notice
    observer = (await notes(client, p["exec"]))["items"]
    assert observer[0]["kind"] == "mention" and "203.0.113.9" not in observer[0]["body"]


async def test_notifications_are_read_one_at_a_time_or_all_at_once(db, client, people, outbox, settle):
    p = await people()
    incident = await declare(client, p["soc"])
    await client.put(
        f"/api/incidents/{incident['id']}/members/{p['soc2'].id}", json={}, headers=p["soc"].headers
    )
    await add_task(client, p["soc"], incident, assignee_id=str(p["soc2"].id))
    await add_task(client, p["soc"], incident, title="Another task", assignee_id=str(p["soc2"].id))
    await settle()
    listed = await notes(client, p["soc2"])
    assert listed["unread"] == 2
    first = listed["items"][0]["id"]
    assert (
        await client.post(f"/api/notifications/{first}/read", headers=p["soc"].headers)
    ).status_code == 404
    await client.post(f"/api/notifications/{first}/read", headers=p["soc2"].headers)
    assert (await notes(client, p["soc2"]))["unread"] == 1
    await client.post("/api/notifications/read-all", headers=p["soc2"].headers)
    assert (await notes(client, p["soc2"]))["unread"] == 0


# ---- playbooks ------------------------------------------------------------------------------------


PHISHING = {
    "name": "Phishing",
    "description": "First hours of a credential phish",
    "trigger_tags": ["phishing"],
    "steps": [
        {
            "title": "Pull the message and its headers",
            "due_minutes": 30,
            "checklist": ["Headers", "Attachments"],
        },
        {"title": "Search for other recipients", "team_id": None, "due_minutes": 60},
        {"title": "Revoke sessions for the user", "team_id": "IDENTITY", "due_minutes": 60},
        {"title": "Check the mailbox rules", "team_id": "IDENTITY"},
    ],
}


async def test_playbooks_are_written_by_leads_and_applied_once_on_declaring(
    db, client, people, outbox, settle
):
    p = await people()
    await connect_channel(client, db, p["admin"], "Incident Response")
    identity = await team_id(db, "Incident Response")
    playbook = {
        **PHISHING,
        "steps": [{**s, "team_id": identity if s.get("team_id") else None} for s in PHISHING["steps"]],
    }
    assert (await client.post("/api/playbooks", json=playbook, headers=p["soc"].headers)).status_code == 403
    made = await client.post("/api/playbooks", json=playbook, headers=p["lead"].headers)
    assert made.status_code == 201, made.text
    made = made.json()
    assert [s["team"]["name"] if s["team"] else None for s in made["steps"]] == [
        None,
        None,
        "Incident Response",
        "Incident Response",
    ]

    incident = await declare(client, p["soc"], tags=["phishing"], playbook_ids=[made["id"]])
    tasks = (await client.get(f"/api/incidents/{incident['id']}/tasks", headers=p["soc"].headers)).json()
    assert len(tasks) == 4 and all(t["playbook_id"] == made["id"] for t in tasks)
    assert tasks[0]["assignee"]["name"] == "Sam Soc" and tasks[0]["due_at"] is not None
    assert tasks[3]["due_at"] is None and tasks[3]["team"]["name"] == "Incident Response"
    detail = (await client.get(f"/api/incidents/{incident['id']}", headers=p["soc"].headers)).json()
    assert "Incident Response" in [t["name"] for t in detail["teams"]]  # the commander brought Identity in
    await settle()
    assert len(outbox.teams()) == 1  # one card for Identity's two tasks, not two
    assert (
        "2 steps of Phishing ready for Incident Response"
        in outbox.teams()[0]["attachments"][0]["content"]["body"][0]["text"]
    )

    again = await client.post(
        f"/api/incidents/{incident['id']}/playbooks/{made['id']}", headers=p["soc"].headers
    )
    assert again.status_code == 409
    applied = (
        await client.get(f"/api/incidents/{incident['id']}/playbooks", headers=p["soc"].headers)
    ).json()
    assert [(a["name"], a["applied_by"]["name"]) for a in applied] == [("Phishing", "Sam Soc")]

    changed = await client.patch(
        f"/api/playbooks/{made['id']}", json={"version": 1, "is_active": False}, headers=p["lead"].headers
    )
    assert changed.json()["is_active"] is False
    other = await declare(client, p["soc"], title="Another phish")
    retired = await client.post(
        f"/api/incidents/{other['id']}/playbooks/{made['id']}", headers=p["soc"].headers
    )
    assert retired.status_code == 409


async def test_task_references_resolve(db, client, people, outbox):
    p = await people()
    incident = await declare(client, p["soc"])
    task = await add_task(client, p["soc"], incident, assignee_id=str(p["soc"].id))
    found = (await client.get(f"/api/refs/{task['ref']}", headers=p["soc"].headers)).json()
    assert (
        found["kind"] == "task"
        and found["title"] == task["title"]
        and found["incident_ref"] == incident["ref"]
    )
    assert (await client.get(f"/api/refs/{task['ref']}", headers=p["dfir"].headers)).status_code == 404
    comment = await client.post(
        f"/api/incidents/{incident['id']}/comments",
        json={"body": "Done?", "target_type": "task", "target_id": task["id"]},
        headers=p["soc"].headers,
    )
    assert comment.status_code == 201


async def test_a_commander_who_is_also_the_team_lead_hears_once(db, client, people, outbox, settle):
    p = await people()
    incident = await declare(client, p["lead"])  # Lee leads SOC and commands the incident
    due = utcnow() - timedelta(hours=30)
    await add_task(client, p["lead"], incident, assignee_id=str(p["soc"].id), due_at=due.isoformat())
    await tick(utcnow())
    await settle()
    assert (await kinds(client, p["lead"])).count("escalated") == 1


async def test_email_can_go_through_microsoft_graph(db, client, people, outbox, settle, monkeypatch):
    settings = get_settings()
    for key, value in {
        "mail_transport": "graph",
        "graph_tenant_id": "contoso.onmicrosoft.com",
        "graph_client_id": "11111111-2222-3333-4444-555555555555",
        "graph_client_secret": "not-a-real-secret",
        "graph_sender": "ostraka@contoso.example",
    }.items():
        monkeypatch.setattr(settings, key, value)
    p = await people()
    incident = await declare(client, p["soc"], title="Beaconing to 203.0.113.9")
    await client.put(
        f"/api/incidents/{incident['id']}/members/{p['soc2'].id}", json={}, headers=p["soc"].headers
    )
    await add_task(client, p["soc"], incident, title="Block 203.0.113.9", assignee_id=str(p["soc2"].id))
    await add_task(client, p["soc"], incident, title="Check proxy logs", assignee_id=str(p["soc2"].id))
    await settle()

    sent = outbox.graph_mail()
    assert len(sent) == 2 and outbox.emails == []  # Graph, not SMTP
    message = sent[0]["message"]
    assert message["toRecipients"] == [{"emailAddress": {"address": "soc2@example.org"}}]
    assert "203.0.113.9" not in message["subject"] and sent[0]["saveToSentItems"] is False
    urls = [url for url, _, _ in outbox.posts]
    assert (
        sum(u.startswith("https://login.microsoftonline.com/contoso.onmicrosoft.com/") for u in urls) == 1
    )  # token reused
    assert "https://graph.microsoft.com/v1.0/users/ostraka%40contoso.example/sendMail" in urls
    auth = next(h for url, _, h in outbox.posts if url.endswith("/sendMail"))["Authorization"]
    assert auth == "Bearer graph-token"
