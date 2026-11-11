"""People, end to end against Postgres: invitations, sign-in with MFA, sessions, tokens, admin."""

import pyotp
import pytest
from sqlalchemy import func, select

from ostraka import cli
from ostraka.models import Role, SecurityEvent, Team, User
from tests.conftest import PASSWORD, SEED_TEAMS

pytestmark = pytest.mark.db

BROWSER = {"X-Ostraka": "1"}


async def team_id(db, name: str) -> str:
    return str(await db.scalar(select(Team.id).where(Team.name == name)))


async def accept(client, link: str, name: str = "New Person", password: str = PASSWORD) -> dict:
    """Follow an invitation link through password, authenticator and backup codes."""
    token = link.rsplit("/", 1)[1]
    r = await client.post(f"/api/invitations/accept/{token}/start", json={"name": name, "password": password})
    assert r.status_code == 200, r.text
    enrol = r.json()
    assert enrol["qr_svg"].startswith("data:image/svg+xml")
    code = pyotp.TOTP(enrol["secret"]).now()
    r = await client.post(f"/api/invitations/accept/{token}/complete", json={"code": code})
    assert r.status_code == 200, r.text
    return r.json()


async def events(db, action: str) -> int:
    return await db.scalar(select(func.count()).where(SecurityEvent.action == action))


# ---- teams and the directory --------------------------------------------------------------------


async def test_seed_teams_exist(db, client, make_person, sign_in):
    me = await sign_in(await make_person())
    r = await client.get("/api/teams", headers=me.headers)
    assert sorted(t["name"] for t in r.json()) == sorted(SEED_TEAMS)


async def test_everything_but_system_endpoints_needs_a_login(client, db):
    for path in ("/api/users/me", "/api/users", "/api/teams", "/api/invitations", "/api/tokens"):
        assert (await client.get(path)).status_code == 401, path


async def test_only_admins_create_teams(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    lead = await sign_in(await make_person("lead@example.org", Role.TEAM_LEAD, "SOC"))
    assert (
        await client.post("/api/teams", json={"name": "Red Team"}, headers=lead.headers)
    ).status_code == 403
    r = await client.post("/api/teams", json={"name": "Red Team"}, headers=admin.headers)
    assert r.status_code == 201
    assert (
        await client.post("/api/teams", json={"name": "Red Team"}, headers=admin.headers)
    ).status_code == 409


# ---- invitations ----------------------------------------------------------------------------------


async def test_invitation_creates_an_account_with_mfa_and_backup_codes(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    r = await client.post(
        "/api/invitations",
        json={
            "email": "New.Person@Example.org",
            "role": "responder",
            "team_id": await team_id(db, "Forensics"),
        },
        headers=admin.headers,
    )
    assert r.status_code == 201, r.text
    link = r.json()["link"]
    assert link.startswith("http://localhost:24747/invite/")

    preview = await client.get(f"/api/invitations/accept/{link.rsplit('/', 1)[1]}")
    assert preview.json()["email"] == "new.person@example.org"
    assert preview.json()["team"] == "Forensics"

    done = await accept(client, link)
    assert len(done["backup_codes"]) == 10
    me = await client.get("/api/users/me", headers={"Authorization": f"Bearer {done['access_token']}"})
    assert me.json()["role"] == "responder"
    assert me.json()["team"]["name"] == "Forensics"
    assert me.json()["backup_codes_left"] == 10

    again = await client.post(
        f"/api/invitations/accept/{link.rsplit('/', 1)[1]}/start", json={"name": "X", "password": PASSWORD}
    )
    assert again.status_code == 404  # one use only
    assert await events(db, "account_created") == 1


async def test_a_newer_invitation_replaces_the_older_link(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    body = {"email": "twice@example.org", "role": "observer", "team_id": await team_id(db, "Leadership")}
    first = (await client.post("/api/invitations", json=body, headers=admin.headers)).json()["link"]
    await client.post("/api/invitations", json=body, headers=admin.headers)
    assert (await client.get(f"/api/invitations/accept/{first.rsplit('/', 1)[1]}")).status_code == 404


async def test_weak_passwords_are_refused_at_acceptance(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    body = {"email": "weak@example.org", "role": "responder", "team_id": await team_id(db, "SOC")}
    link = (await client.post("/api/invitations", json=body, headers=admin.headers)).json()["link"]
    r = await client.post(
        f"/api/invitations/accept/{link.rsplit('/', 1)[1]}/start", json={"name": "W", "password": "short"}
    )
    assert r.status_code == 422


async def test_team_leads_invite_only_into_their_own_team(db, client, make_person, sign_in):
    lead = await sign_in(await make_person("lead@example.org", Role.TEAM_LEAD, "SOC"))
    soc, dfir = await team_id(db, "SOC"), await team_id(db, "Forensics")

    ok = await client.post(
        "/api/invitations",
        json={"email": "a@example.org", "role": "responder", "team_id": soc},
        headers=lead.headers,
    )
    assert ok.status_code == 201
    other_team = await client.post(
        "/api/invitations",
        json={"email": "b@example.org", "role": "responder", "team_id": dfir},
        headers=lead.headers,
    )
    assert other_team.status_code == 403
    another_lead = await client.post(
        "/api/invitations",
        json={"email": "c@example.org", "role": "team_lead", "team_id": soc},
        headers=lead.headers,
    )
    assert another_lead.status_code == 403
    listed = (await client.get("/api/invitations", headers=lead.headers)).json()
    assert [i["email"] for i in listed] == ["a@example.org"]


async def test_responders_cannot_invite(db, client, make_person, sign_in):
    responder = await sign_in(await make_person())
    r = await client.post(
        "/api/invitations",
        json={"email": "z@example.org", "role": "responder", "team_id": await team_id(db, "SOC")},
        headers=responder.headers,
    )
    assert r.status_code == 403


async def test_existing_accounts_cannot_be_invited_again(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    await make_person("taken@example.org")
    r = await client.post(
        "/api/invitations",
        json={"email": "taken@example.org", "role": "responder", "team_id": await team_id(db, "SOC")},
        headers=admin.headers,
    )
    assert r.status_code == 409


# ---- signing in -----------------------------------------------------------------------------------


async def test_password_alone_never_opens_a_session(db, client, make_person):
    person = await make_person()
    r = await client.post("/api/auth/login", json={"email": person.user.email, "password": PASSWORD})
    assert r.json()["mfa_required"] is True
    assert "access_token" not in r.json()
    assert (
        await client.get("/api/users/me", headers={"Authorization": f"Bearer {r.json()['ticket']}"})
    ).status_code == 401


async def test_wrong_password_and_unknown_email_look_the_same(db, client, make_person):
    await make_person()
    wrong = await client.post(
        "/api/auth/login", json={"email": "analyst@example.org", "password": "nope" * 4}
    )
    unknown = await client.post(
        "/api/auth/login", json={"email": "nobody@example.org", "password": "nope" * 4}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_a_totp_code_cannot_be_replayed(db, client, make_person):
    person = await make_person()
    code = person.code()
    for expected in (200, 401):
        ticket = (
            await client.post("/api/auth/login", json={"email": person.user.email, "password": PASSWORD})
        ).json()["ticket"]
        r = await client.post("/api/auth/mfa", json={"ticket": ticket, "code": code})
        assert r.status_code == expected


async def test_backup_codes_work_once(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    body = {"email": "backup@example.org", "role": "responder", "team_id": await team_id(db, "SOC")}
    link = (await client.post("/api/invitations", json=body, headers=admin.headers)).json()["link"]
    code = (await accept(client, link))["backup_codes"][0]

    for expected in (200, 401):
        ticket = (
            await client.post("/api/auth/login", json={"email": "backup@example.org", "password": PASSWORD})
        ).json()["ticket"]
        r = await client.post("/api/auth/mfa", json={"ticket": ticket, "code": code.lower()})
        assert r.status_code == expected
    assert await events(db, "backup_code_used") == 1


async def test_five_wrong_passwords_lock_the_account(db, client, make_person):
    person = await make_person()
    for _ in range(5):
        await client.post("/api/auth/login", json={"email": person.user.email, "password": "wrong" * 4})
    r = await client.post("/api/auth/login", json={"email": person.user.email, "password": PASSWORD})
    assert r.status_code == 401
    assert await events(db, "account_locked") == 1


# ---- sessions -------------------------------------------------------------------------------------


async def test_refresh_rotates_and_a_replayed_cookie_ends_the_session(db, client, make_person, sign_in):
    await sign_in(await make_person())
    first = client.cookies.get("ostraka_refresh")
    assert first

    assert (await client.post("/api/auth/refresh")).status_code == 403  # no X-Ostraka header
    r = await client.post("/api/auth/refresh", headers=BROWSER)
    assert r.status_code == 200
    second = client.cookies.get("ostraka_refresh")
    assert second and second != first

    client.cookies.set("ostraka_refresh", first, path="/api/auth")
    assert (await client.post("/api/auth/refresh", headers=BROWSER)).status_code == 401
    client.cookies.set("ostraka_refresh", second, path="/api/auth")
    assert (
        await client.post("/api/auth/refresh", headers=BROWSER)
    ).status_code == 401  # whole family revoked


async def test_logout_ends_the_session_and_its_access_token(db, client, make_person, sign_in):
    me = await sign_in(await make_person())
    assert (await client.get("/api/users/me", headers=me.headers)).status_code == 200
    assert (await client.post("/api/auth/logout", headers=BROWSER)).status_code == 204
    assert (await client.get("/api/users/me", headers=me.headers)).status_code == 401


async def test_changing_password_needs_the_current_one(db, client, make_person, sign_in):
    me = await sign_in(await make_person())
    bad = await client.post(
        "/api/users/me/password",
        json={"current_password": "wrong", "new_password": "a brand new passphrase"},
        headers=me.headers,
    )
    assert bad.status_code == 403
    ok = await client.post(
        "/api/users/me/password",
        json={"current_password": PASSWORD, "new_password": "a brand new passphrase"},
        headers=me.headers,
    )
    assert ok.status_code == 204
    assert (await client.get("/api/users/me", headers=me.headers)).status_code == 200  # this browser stays in


async def test_new_backup_codes_need_an_authenticator_code(db, client, make_person, sign_in):
    me = await sign_in(await make_person())
    assert (
        await client.post("/api/users/me/backup-codes", json={"code": "000000"}, headers=me.headers)
    ).status_code == 403
    r = await client.post("/api/users/me/backup-codes", json={"code": me.code()}, headers=me.headers)
    assert r.status_code == 200 and len(r.json()["backup_codes"]) == 10


# ---- preferences ----------------------------------------------------------------------------------


async def test_theme_preferences_are_saved(db, client, make_person, sign_in):
    me = await sign_in(await make_person())
    r = await client.patch(
        "/api/users/me", json={"theme_palette": "ledger", "theme_mode": "dark"}, headers=me.headers
    )
    assert (r.json()["theme_palette"], r.json()["theme_mode"]) == ("ledger", "dark")
    assert (
        await client.patch("/api/users/me", json={"theme_palette": "neon"}, headers=me.headers)
    ).status_code == 422


async def test_observers_start_with_raw_iocs_hidden(db, client, make_person, sign_in):
    observer = await sign_in(await make_person("exec@example.org", Role.OBSERVER, "Leadership"))
    me = (await client.get("/api/users/me", headers=observer.headers)).json()
    assert me["show_raw_iocs"] is False
    r = await client.patch("/api/users/me", json={"show_raw_iocs": True}, headers=observer.headers)
    assert r.json()["show_raw_iocs"] is True


# ---- admin changes --------------------------------------------------------------------------------


async def test_deactivating_someone_ends_their_sessions(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    leaver = await make_person("leaver@example.org")
    ticket = (
        await client.post("/api/auth/login", json={"email": "leaver@example.org", "password": PASSWORD})
    ).json()
    granted = await client.post("/api/auth/mfa", json={"ticket": ticket["ticket"], "code": leaver.code()})
    leaver_headers = {"Authorization": f"Bearer {granted.json()['access_token']}"}
    assert (await client.get("/api/users/me", headers=leaver_headers)).status_code == 200

    r = await client.patch(f"/api/users/{leaver.id}", json={"is_active": False}, headers=admin.headers)
    assert r.status_code == 200
    assert (await client.get("/api/users/me", headers=leaver_headers)).status_code == 401


async def test_admins_cannot_demote_or_deactivate_themselves(db, client, make_person, sign_in):
    """So ostraka always keeps at least one admin: removing one takes another."""
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    for change in ({"is_active": False}, {"role": "responder", "team_id": await team_id(db, "SOC")}):
        assert (
            await client.patch(f"/api/users/{admin.id}", json=change, headers=admin.headers)
        ).status_code == 403
    count = await db.scalar(select(func.count()).where(User.role == Role.ADMIN, User.is_active.is_(True)))
    assert count == 1


async def test_team_needed_for_everyone_but_admins(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    other_admin = await make_person("admin2@example.org", Role.ADMIN, None)
    r = await client.patch(f"/api/users/{other_admin.id}", json={"role": "responder"}, headers=admin.headers)
    assert r.status_code == 422
    r = await client.patch(
        f"/api/users/{other_admin.id}",
        json={"role": "team_lead", "team_id": await team_id(db, "Forensics")},
        headers=admin.headers,
    )
    assert r.status_code == 200 and r.json()["role"] == "team_lead"


async def test_reset_access_issues_a_link_and_ends_sessions(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    target = await make_person("lost@example.org")
    r = await client.post(f"/api/users/{target.id}/reset-access", headers=admin.headers)
    assert r.status_code == 201 and r.json()["is_reset"] is True
    done = await accept(client, r.json()["link"], name="Lost Person", password="a completely new passphrase")
    me = await client.get("/api/users/me", headers={"Authorization": f"Bearer {done['access_token']}"})
    assert me.json()["email"] == "lost@example.org"
    login = await client.post("/api/auth/login", json={"email": "lost@example.org", "password": PASSWORD})
    assert login.status_code == 401  # the old password no longer works


# ---- API tokens -----------------------------------------------------------------------------------


async def test_api_tokens(db, client, make_person, sign_in):
    me = await sign_in(await make_person())
    full = await client.post("/api/tokens", json={"name": "script"}, headers=me.headers)
    assert full.status_code == 201
    raw = full.json()["token"]
    assert raw.startswith("ost_") and full.json()["prefix"] == raw[:10]
    as_token = {"Authorization": f"Bearer {raw}"}
    assert (await client.get("/api/users/me", headers=as_token)).status_code == 200
    assert (await client.post("/api/tokens", json={"name": "another"}, headers=as_token)).status_code == 403

    mcp = (await client.post("/api/tokens", json={"name": "mcp", "scope": "mcp"}, headers=me.headers)).json()[
        "token"
    ]
    read_only = {"Authorization": f"Bearer {mcp}"}
    assert (await client.get("/api/teams", headers=read_only)).status_code == 200
    assert (await client.patch("/api/users/me", json={"name": "x"}, headers=read_only)).status_code == 403

    token_id = full.json()["id"]
    assert (await client.delete(f"/api/tokens/{token_id}", headers=me.headers)).status_code == 204
    assert (await client.get("/api/users/me", headers=as_token)).status_code == 401


# ---- the first admin ------------------------------------------------------------------------------


async def test_bootstrap_admin_refuses_when_an_admin_exists(db, make_person, capsys):
    assert await cli._bootstrap_admin("first@example.org", force=False) == 0
    assert "/invite/" in capsys.readouterr().out
    await make_person("admin@example.org", Role.ADMIN, None)
    assert await cli._bootstrap_admin("second@example.org", force=False) == cli.ADMIN_EXISTS
