"""`ostraka import`: the sample file loads through the API as its people, keeps every record's activity
chain intact, sends nothing outside ostraka, and `--remove` takes away exactly what it loaded."""

import json

import pytest
from sqlalchemy import func, select

from ostraka import importer
from ostraka.models import ApiToken, Incident, Role, User

pytestmark = pytest.mark.db


async def test_the_sample_loads_with_every_feature_it_shows(
    db, seed_playbooks, client, outbox, settle, make_person, sign_in
):
    outcome = await importer.run(importer.SAMPLE, http=client)
    await settle()

    assert len(outcome.records) == 7 and outcome.people_created == 21
    kinds = dict((await db.execute(select(Incident.kind, func.count()).group_by(Incident.kind))).all())
    assert {k.value: n for k, n in kinds.items()} == {"incident": 4, "hunt": 3}
    # The importer's tokens are all revoked, and nothing left ostraka: placeholders have no email or push
    assert not await db.scalar(
        select(func.count()).select_from(ApiToken).where(ApiToken.revoked_at.is_(None))
    )
    assert outbox.emails == [] and outbox.posts == []

    admin = await make_person("admin@example.org", Role.ADMIN, None, name="Ada Admin")
    await sign_in(admin)
    records = [
        *(await client.get("/api/incidents?status=open", headers=admin.headers)).json(),
        *(await client.get("/api/incidents?status=closed", headers=admin.headers)).json(),
    ]
    assert len(records) == 7
    assert all("sample" in r["tags"] for r in records)
    for record in records:
        chain = (
            await client.get(f"/api/incidents/{record['id']}/activity/verify", headers=admin.headers)
        ).json()
        assert chain["ok"], (record["ref"], chain)

    by_title = {r["title"]: r for r in records}
    ransomware = by_title["Ransomware on the finance file servers"]
    links = (await client.get(f"/api/incidents/{ransomware['id']}/links", headers=admin.headers)).json()
    assert [c["title"] for c in links["children"]] == [
        "Service account svc_sqlbackup used from a compromised workstation"
    ]
    tasks = (await client.get(f"/api/incidents/{ransomware['id']}/tasks", headers=admin.headers)).json()
    krbtgt = next(t for t in tasks if t["title"].startswith("Reset krbtgt"))
    assert [b["title"] for b in krbtgt["blocked_by"]] == ["Rebuild DC02 from known-good media"]
    assert any(t["parent_id"] for t in tasks) and any(t["overdue"] for t in tasks)
    findings = (await client.get(f"/api/incidents/{ransomware['id']}/findings", headers=admin.headers)).json()
    assert {f["status"] for f in findings} == {"confirmed", "lead"}
    ico, insurer = (
        await client.get(f"/api/incidents/{ransomware['id']}/clocks", headers=admin.headers)
    ).json()
    assert ico["regime"] == "uk_gdpr" and ico["owner"]["name"] == "Nina Patel"
    assert ico["milestones"][0]["state"] == "met" and ico["milestones"][0]["reference"].startswith("ICO case")
    assert [m["state"] for m in insurer["milestones"]] == ["met", "running"]

    hunt = by_title["Kerberoasting against service accounts"]
    assert hunt["kind"] == "hunt" and hunt["status"] == "closed" and hunt["phase"] == "conclude"
    leak = by_title["Driver onboarding folder shared with anyone who has the link"]
    assert leak["status"] == "closed" and leak["phase"] == "learn"


async def test_placeholder_people_cannot_sign_in(db, seed_playbooks, client):
    await importer.run(importer.SAMPLE, http=client)
    r = await client.post(
        "/api/auth/login",
        json={"email": "amara.okafor@sample.example.org", "password": "correct horse battery staple"},
    )
    assert r.status_code == 401


async def test_removing_the_sample_takes_away_only_what_it_loaded(
    db, seed_playbooks, client, make_person, sign_in
):
    from tests.test_incidents import declare

    real = await make_person("real@example.org", Role.RESPONDER, "SOC", name="Real Person")
    await sign_in(real)
    kept = await declare(client, real, title="A real incident, not a sample")
    await importer.run(importer.SAMPLE, http=client)
    ours = await db.scalar(select(func.count()).select_from(Incident))
    removed, people, _ = await importer.remove(importer.SAMPLE)
    assert removed == ours - 1 == 7 and people == 21
    remaining = (await db.scalars(select(Incident.title))).all()
    assert remaining == [kept["title"]]
    assert not await db.scalar(
        select(func.count()).select_from(User).where(User.email.like("%@sample.example.org"))
    )


async def test_a_bad_file_says_where_and_why(tmp_path, db, client):
    data = json.loads(importer.SAMPLE.read_text())
    data["records"][0]["steps"][1]["at"] = "-30d 09:00"  # before the step above it
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    with pytest.raises(importer.ImportProblem, match="comes before the step above it"):
        await importer.run(path, http=client)

    path.write_text(json.dumps({"format": "something-else"}))
    with pytest.raises(importer.ImportProblem, match="not an Ostraka import file"):
        await importer.run(path, http=client)


def test_times():
    from datetime import UTC, datetime

    now = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    before = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
    assert importer.when("-2d 07:52", now=now, previous=before) == datetime(2026, 11, 8, 7, 52, tzinfo=UTC)
    assert importer.when("-3h", now=now, previous=before) == datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    assert importer.when("+1h 30m", now=now, previous=before) == datetime(2026, 11, 8, 10, 30, tzinfo=UTC)
    assert importer.when(None, now=now, previous=before) == datetime(2026, 11, 8, 9, 2, tzinfo=UTC)
    with pytest.raises(importer.ImportProblem):
        importer.when("next tuesday", now=now, previous=before)
