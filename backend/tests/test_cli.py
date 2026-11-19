"""The backend's own command line: what the host `ostraka` command and the launchers rely on."""

from __future__ import annotations

from sqlalchemy import select

from ostraka import cli
from ostraka.models import Invitation, Role


async def test_bootstrap_admin_link_only_prints_just_the_link(db, make_person, capsys):
    assert await cli._bootstrap_admin("first@example.org", force=False, link_only=True) == 0
    out = capsys.readouterr().out
    # A launcher opens whatever is printed, so it must be the link and only the link
    assert out.count("\n") == 1
    assert out.strip().startswith("http") and "/invite#" in out
    assert "first@example.org" not in out

    await make_person("admin@example.org", Role.ADMIN, None)
    assert await cli._bootstrap_admin("second@example.org", force=False, link_only=True) == cli.ADMIN_EXISTS
    refused = capsys.readouterr()
    assert refused.out == "" and "already has an admin" in refused.err


async def test_has_admin(db, make_person, capsys):
    assert await cli._has_admin() == 0
    assert capsys.readouterr().out == "false\n"
    await make_person("admin@example.org", Role.ADMIN, None)
    assert await cli._has_admin() == 0
    assert capsys.readouterr().out == "true\n"


def test_link_only_reaches_bootstrap_admin(monkeypatch):
    seen: list[tuple[str, bool, bool]] = []

    async def fake(email: str, force: bool, link_only: bool = False) -> int:
        seen.append((email, force, link_only))
        return 0

    monkeypatch.setattr(cli, "_bootstrap_admin", fake)
    assert cli.main(["bootstrap-admin", "--link-only", "First@Example.org"]) == 0
    assert cli.main(["bootstrap-admin", "first@example.org"]) == 0
    assert seen == [("first@example.org", False, True), ("first@example.org", False, False)]


async def test_only_the_newest_bootstrap_link_works(db, capsys):
    await cli._bootstrap_admin("first@example.org", force=False, link_only=True)
    await cli._bootstrap_admin("someone-else@example.org", force=False, link_only=True)
    capsys.readouterr()
    rows = (await db.execute(select(Invitation).order_by(Invitation.created_at))).scalars().all()
    assert [(i.email, i.revoked_at is None) for i in rows] == [
        ("first@example.org", False),
        ("someone-else@example.org", True),
    ]


async def test_reset_access_gives_a_way_back_in_and_ends_sign_ins(db, client, make_person, sign_in, capsys):
    from ostraka.models import SecurityEvent, Session

    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    assert (await client.get("/api/users/me", headers=admin.headers)).status_code == 200

    assert await cli._reset_access("admin@example.org") == 0
    first = capsys.readouterr().out.split()[-1]
    assert await cli._reset_access("admin@example.org") == 0
    link = capsys.readouterr().out.split()[-1]
    assert "/invite#" in link

    # The reset link opens as a reset of that account, and only the newest works
    r = await client.post("/api/invitations/accept", json={"token": link.split("#", 1)[1]})
    assert r.status_code == 200 and r.json()["is_reset"] is True
    assert (
        await client.post("/api/invitations/accept", json={"token": first.split("#", 1)[1]})
    ).status_code == 404

    me = admin.user.id
    db.expire_all()
    live = select(Session).where(Session.user_id == me, Session.revoked_at.is_(None))
    assert await db.scalar(live) is None
    issued = select(SecurityEvent).where(SecurityEvent.action == "access_reset_issued")
    events = (await db.execute(issued)).scalars()
    assert [(e.subject_id, e.actor_id, e.detail) for e in events] == [
        (me, None, {"via": "cli", "tokens_revoked": 0})
    ] * 2


async def test_reset_access_refuses_an_unknown_or_deactivated_account(db, make_person, capsys):
    assert await cli._reset_access("nobody@example.org") == 1
    assert "no account" in capsys.readouterr().err

    gone = await make_person("gone@example.org", Role.RESPONDER)
    gone.user.is_active = False
    await db.commit()
    assert await cli._reset_access("gone@example.org") == 1
    assert "deactivated" in capsys.readouterr().err
    assert await db.scalar(select(Invitation).where(Invitation.user_id == gone.user.id)) is None


def test_reset_access_takes_the_email_as_written(monkeypatch):
    seen: list[str] = []

    async def fake(email: str) -> int:
        seen.append(email)
        return 0

    monkeypatch.setattr(cli, "_reset_access", fake)
    assert cli.main(["reset-access", " Admin@Example.org "]) == 0
    assert seen == ["admin@example.org"]
