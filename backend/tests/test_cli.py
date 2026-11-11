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
    assert out.strip().startswith("http") and "/invite/" in out
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
