"""The account standard's links, end to end against Postgres: claimed once, the same authenticator
on every start, expiry said plainly, pending choices cleared, the inviter still able to invite, wrong
codes closing an invitation, Forgot password, link addresses, and deactivation."""

import asyncio
from datetime import timedelta

import pyotp
import pytest
from sqlalchemy import func, select

from ostraka import accounts, cli
from ostraka.auth import lockout
from ostraka.config import get_settings
from ostraka.models import ApiToken, Invitation, Role, SecurityEvent, Session, Team, User, utcnow
from tests.conftest import PASSWORD

pytestmark = pytest.mark.db

BROWSER = {"X-Ostraka": "1"}
NEW_PASSWORD = "a completely new passphrase"


async def soc(db) -> str:
    return str(await db.scalar(select(Team.id).where(Team.name == "SOC")))


def token_of(link: str) -> str:
    return link.split("#", 1)[1]


async def events(db, action: str) -> list[SecurityEvent]:
    return list((await db.scalars(select(SecurityEvent).where(SecurityEvent.action == action))).all())


async def invite(client, db, by, email="new@example.org", role="responder") -> str:
    body = {"email": email, "role": role, "team_id": await soc(db)}
    r = await client.post("/api/invitations", json=body, headers=by.headers)
    assert r.status_code == 201, r.text
    return token_of(r.json()["link"])


async def start(client, token: str, name: str = "New Person", password: str = PASSWORD) -> str:
    r = await client.post(
        "/api/invitations/accept/start", json={"token": token, "name": name, "password": password}
    )
    assert r.status_code == 200, r.text
    return r.json()["secret"]


async def complete(client, token: str, secret: str):
    return await client.post(
        "/api/invitations/accept/complete", json={"token": token, "code": pyotp.TOTP(secret).now()}
    )


async def row(db, token: str) -> Invitation:
    from ostraka.auth.secrets import token_hash

    db.expire_all()
    return await db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(token)))


def cleared(invitation: Invitation) -> bool:
    return (
        invitation.pending_name is None
        and invitation.pending_password_hash is None
        and invitation.pending_totp_secret is None
    )


@pytest.fixture
def fresh_counters(monkeypatch):
    """Throttle and lockout counts of this test only: the in-memory store outlives a test."""
    monkeypatch.setattr(lockout, "_store", None)


@pytest.fixture
def email_on(monkeypatch, outbox):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "mail.example.org")
    monkeypatch.setattr(settings, "smtp_from", "ostraka@example.org")
    monkeypatch.setattr(settings, "public_url", "https://ostraka.example.org")
    return outbox


# ---- item 2: claimed once --------------------------------------------------------------------


async def test_two_completions_at_once_cannot_both_succeed(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    target = await make_person("lost@example.org")
    link = (await client.post(f"/api/users/{target.id}/reset-access", headers=admin.headers)).json()["link"]
    token = token_of(link)
    secret = await start(client, token, "Lost", NEW_PASSWORD)
    first, second = await asyncio.gather(complete(client, token, secret), complete(client, token, secret))
    assert sorted([first.status_code, second.status_code])[0] == 200
    assert [first.status_code, second.status_code].count(200) == 1
    assert len(await events(db, "access_reset_completed")) == 1


async def test_a_claim_changes_the_row_only_once(db, client, make_person, sign_in):
    from ostraka.db import sessions

    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    invitation = await row(db, await invite(client, db, admin))
    # Two sessions, each having read the link while it was open
    async with sessions()() as one, sessions()() as two:
        assert await accounts.claim(one, invitation.id) is True
        await one.commit()
        assert await accounts.claim(two, invitation.id) is False


# ---- item 3: the same authenticator on every start ------------------------------------------


async def test_starting_again_keeps_the_authenticator_and_takes_the_new_choices(
    db, client, make_person, sign_in
):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    token = await invite(client, db, admin)
    first = await start(client, token, "First Name", PASSWORD)
    again = await start(client, token, "Second Name", NEW_PASSWORD)
    assert again == first
    assert (await complete(client, token, first)).status_code == 200
    user = await db.scalar(select(User).where(User.email == "new@example.org"))
    assert user.name == "Second Name"
    login = await client.post("/api/auth/login", json={"email": "new@example.org", "password": NEW_PASSWORD})
    assert login.status_code == 200


async def test_a_reset_started_twice_shows_the_same_authenticator(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    target = await make_person("lost@example.org")
    link = (await client.post(f"/api/users/{target.id}/reset-access", headers=admin.headers)).json()["link"]
    assert await start(client, token_of(link), "Lost", NEW_PASSWORD) == await start(
        client, token_of(link), "Lost", NEW_PASSWORD
    )


# ---- item 4: an expired link says so ---------------------------------------------------------


async def test_an_expired_link_says_it_has_expired_and_forgets_what_was_chosen(
    db, client, make_person, sign_in
):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    token = await invite(client, db, admin)
    await start(client, token)
    invitation = await row(db, token)
    invitation.expires_at = utcnow() - timedelta(hours=23)
    await db.commit()

    r = await client.post("/api/invitations/accept", json={"token": token})
    assert r.status_code == 410 and "expired" in r.json()["detail"]
    assert cleared(await row(db, token))

    unknown = await client.post("/api/invitations/accept", json={"token": "x" * 43})
    assert unknown.status_code == 404 and "expired" not in unknown.json()["detail"]


async def test_used_replaced_and_withdrawn_links_get_the_one_message(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    replaced = await invite(client, db, admin)
    newest = await invite(client, db, admin)
    withdrawn = await invite(client, db, admin, email="other@example.org")
    await client.delete(f"/api/invitations/{(await row(db, withdrawn)).id}", headers=admin.headers)
    assert (await complete(client, newest, await start(client, newest))).status_code == 200

    answers = [
        await client.post("/api/invitations/accept", json={"token": t}) for t in (replaced, withdrawn, newest)
    ]
    assert {r.status_code for r in answers} == {404}
    assert len({r.json()["detail"] for r in answers}) == 1


# ---- item 5: pending choices cleared ----------------------------------------------------------


async def test_pending_choices_go_when_a_link_is_replaced_withdrawn_or_used(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    replaced = await invite(client, db, admin)
    await start(client, replaced)
    used = await invite(client, db, admin)
    await start(client, used)
    assert cleared(await row(db, replaced))

    withdrawn = await invite(client, db, admin, email="other@example.org")
    await start(client, withdrawn)
    await client.delete(f"/api/invitations/{(await row(db, withdrawn)).id}", headers=admin.headers)
    assert cleared(await row(db, withdrawn))

    assert (await complete(client, used, await start(client, used))).status_code == 200
    assert cleared(await row(db, used))


async def test_a_superseded_first_admin_link_forgets_what_was_chosen(db, client, capsys):
    await cli._bootstrap_admin("first@example.org", force=False, link_only=True)
    first = token_of(capsys.readouterr().out.strip())
    await start(client, first, "First", PASSWORD)
    await cli._bootstrap_admin("second@example.org", force=False, link_only=True)
    superseded = await row(db, first)
    assert superseded.revoked_at is not None and cleared(superseded)
    assert [e.detail["reason"] for e in await events(db, "invitation_revoked")] == ["replaced"]


async def test_a_completed_reset_drops_an_authenticator_change_begun_before_it(
    db, client, make_person, sign_in
):
    from ostraka.auth.secrets import encrypt

    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    target = await make_person("lost@example.org")
    target_id = target.id
    target.user.pending_totp_secret = encrypt(pyotp.random_base32())
    await db.commit()
    link = (await client.post(f"/api/users/{target.id}/reset-access", headers=admin.headers)).json()["link"]
    token = token_of(link)
    assert (
        await complete(client, token, await start(client, token, "Lost", NEW_PASSWORD))
    ).status_code == 200
    db.expire_all()
    assert (await db.get(User, target_id)).pending_totp_secret is None


# ---- item 6: the inviter must still be able to invite -----------------------------------------


async def test_a_link_from_someone_since_deactivated_or_demoted_is_refused(db, client, make_person, sign_in):
    lead = await sign_in(await make_person("lead@example.org", Role.TEAM_LEAD, "SOC"))
    from_lead = await invite(client, db, lead)
    secret = await start(client, from_lead)

    lead.user.role = Role.RESPONDER
    await db.commit()
    refused = await complete(client, from_lead, secret)
    assert refused.status_code == 403 and "can no longer" in refused.json()["detail"]

    lead.user.role, lead.user.is_active = Role.TEAM_LEAD, False
    await db.commit()
    refused = await client.post("/api/invitations/accept", json={"token": from_lead})
    assert refused.status_code == 403 and "no longer has an account" in refused.json()["detail"]
    assert await db.scalar(select(func.count()).select_from(User).where(User.email == "new@example.org")) == 0

    # A reset from an admin who has since been demoted
    other_admin = await sign_in(await make_person("admin2@example.org", Role.ADMIN, None))
    target = await make_person("lost@example.org")
    link = (await client.post(f"/api/users/{target.id}/reset-access", headers=other_admin.headers)).json()[
        "link"
    ]
    other_admin.user.role, other_admin.user.team_id = Role.RESPONDER, lead.user.team_id
    await db.commit()
    assert (await client.post("/api/invitations/accept", json={"token": token_of(link)})).status_code == 403


async def test_server_issued_links_have_no_inviter_to_check(db, client, make_person, capsys):
    await make_person("lost@example.org")
    assert await cli._reset_access("lost@example.org") == 0
    link = capsys.readouterr().out.split()[-1]
    assert (await client.post("/api/invitations/accept", json={"token": token_of(link)})).status_code == 200


# ---- item 7: five wrong codes close an invitation ---------------------------------------------


async def test_five_wrong_codes_close_an_invitation(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    token = await invite(client, db, admin)
    secret = await start(client, token)
    for attempt in range(1, 6):
        r = await client.post("/api/invitations/accept/complete", json={"token": token, "code": "000000"})
        assert r.status_code == 422
        assert ("closed" in r.json()["detail"]) is (attempt == 5)
    assert len(await events(db, "invitation_wrong_code")) == 5
    assert len(await events(db, "invitation_closed")) == 1
    closed = await row(db, token)
    assert closed.revoked_at is not None and cleared(closed)

    after = await complete(client, token, secret)
    assert after.status_code == 404 and "closed" in after.json()["detail"]
    # A new invitation from the admin works
    fresh = await invite(client, db, admin)
    assert (await complete(client, fresh, await start(client, fresh))).status_code == 200


# ---- item 12: Forgot password ------------------------------------------------------------------


async def test_forgot_password_emails_a_reset_link_and_ends_nothing_until_it_is_used(
    db, client, make_person, sign_in, email_on, settle, fresh_counters
):
    person = await sign_in(await make_person("lost@example.org"))
    person_id = person.id
    made = await client.post("/api/tokens", json={"name": "script"}, headers=person.headers)
    script = {"Authorization": f"Bearer {made.json()['token']}"}
    assert (await client.get("/api/auth/forgot")).json() == {"available": True}

    known = await client.post("/api/auth/forgot", json={"email": "Lost@Example.org"}, headers=BROWSER)
    unknown = await client.post("/api/auth/forgot", json={"email": "nobody@example.org"}, headers=BROWSER)
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    await settle()
    assert len(email_on.emails) == 1
    mail = email_on.emails[0]
    assert mail["To"] == "lost@example.org"
    link = next(w for w in mail.get_content().split() if "/invite#" in w)
    assert link.startswith("https://ostraka.example.org/invite#")

    # Nothing ends at the request: the old password, sessions and tokens all still work
    assert (await client.get("/api/users/me", headers=script)).status_code == 200
    assert (await client.get("/api/users/me", headers=person.headers)).status_code == 200
    login = await client.post("/api/auth/login", json={"email": "lost@example.org", "password": PASSWORD})
    assert login.status_code == 200
    issued = await events(db, "access_reset_issued")
    assert [(e.subject_id, e.detail["via"]) for e in issued] == [(person_id, "self-service")]

    token = token_of(link)
    assert (await client.post("/api/invitations/accept", json={"token": token})).json()["is_reset"] is True
    assert (
        await complete(client, token, await start(client, token, "Lost", NEW_PASSWORD))
    ).status_code == 200
    assert (await client.get("/api/users/me", headers=script)).status_code == 401
    db.expire_all()
    live = select(Session).where(Session.user_id == person_id, Session.revoked_at.is_(None))
    assert len((await db.scalars(live)).all()) == 1  # only the one the reset just opened
    assert (
        await db.scalar(select(ApiToken).where(ApiToken.user_id == person_id, ApiToken.revoked_at.is_(None)))
        is None
    )
    done = await events(db, "access_reset_completed")
    assert done[0].detail["via"] == "self-service" and done[0].detail["tokens_revoked"] == 1


async def test_a_forged_origin_or_host_never_reaches_the_emailed_link(
    db, client, make_person, email_on, settle, fresh_counters
):
    await make_person("lost@example.org")
    forged = {
        **BROWSER,
        "Origin": "https://evil.example.net",
        "Host": "evil.example.net",
        "X-Forwarded-Host": "evil.example.net",
        "X-Forwarded-Proto": "https",
    }
    r = await client.post("/api/auth/forgot", json={"email": "lost@example.org"}, headers=forged)
    assert r.status_code == 200
    await settle()
    text = email_on.emails[0].get_content()
    assert "evil.example.net" not in text
    assert "https://ostraka.example.org/invite#" in text


async def test_forgot_password_never_replaces_an_admins_reset(
    db, client, make_person, sign_in, email_on, settle, fresh_counters
):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    target = await make_person("lost@example.org")
    link = (await client.post(f"/api/users/{target.id}/reset-access", headers=admin.headers)).json()["link"]
    r = await client.post("/api/auth/forgot", json={"email": "lost@example.org"}, headers=BROWSER)
    assert r.status_code == 200
    await settle()
    assert email_on.emails == []
    assert (await client.post("/api/invitations/accept", json={"token": token_of(link)})).status_code == 200


async def test_a_second_forgot_request_replaces_the_first(
    db, client, make_person, email_on, settle, fresh_counters
):
    await make_person("lost@example.org")
    for _ in range(2):
        await client.post("/api/auth/forgot", json={"email": "lost@example.org"}, headers=BROWSER)
    await settle()
    first, second = (next(w for w in m.get_content().split() if "#" in w) for m in email_on.emails)
    assert (await client.post("/api/invitations/accept", json={"token": token_of(first)})).status_code == 404
    assert (await client.post("/api/invitations/accept", json={"token": token_of(second)})).status_code == 200
    assert [e.detail["reason"] for e in await events(db, "invitation_revoked")] == ["replaced"]


async def test_forgot_password_sends_nothing_to_deactivated_or_imported_people(
    db, client, make_person, email_on, settle, fresh_counters
):
    gone = await make_person("gone@example.org")
    gone.user.is_active = False
    imported = await make_person("imported@example.org")
    imported.user.password_hash = accounts.UNUSABLE_PASSWORD
    await db.commit()
    for email in ("gone@example.org", "imported@example.org"):
        r = await client.post("/api/auth/forgot", json={"email": email}, headers=BROWSER)
        assert r.status_code == 200
    await settle()
    assert email_on.emails == [] and await events(db, "access_reset_issued") == []


async def test_forgot_password_is_rate_limited_per_address_and_per_ip(
    db, client, make_person, email_on, fresh_counters
):
    from ostraka.api import auth

    for _ in range(auth.FORGOT_PER_EMAIL):
        r = await client.post("/api/auth/forgot", json={"email": "a@example.org"}, headers=BROWSER)
        assert r.status_code == 200
    r = await client.post("/api/auth/forgot", json={"email": "a@example.org"}, headers=BROWSER)
    assert r.status_code == 429
    # From another address, a different email still goes through, until that address has asked too often
    elsewhere = {**BROWSER, "X-Forwarded-For": "198.51.100.20"}
    for n in range(auth.FORGOT_PER_IP):
        r = await client.post("/api/auth/forgot", json={"email": f"p{n}@example.org"}, headers=elsewhere)
        assert r.status_code == 200
    r = await client.post("/api/auth/forgot", json={"email": "q@example.org"}, headers=elsewhere)
    assert r.status_code == 429


async def test_forgot_password_needs_email_and_the_header(db, client, monkeypatch, fresh_counters):
    assert (await client.get("/api/auth/forgot")).json() == {"available": False}
    r = await client.post("/api/auth/forgot", json={"email": "a@example.org"}, headers=BROWSER)
    assert r.status_code == 503 and "administrator" in r.json()["detail"]

    # Email without OSTRAKA_PUBLIC_URL isn't enough: the link would be built from a Host header anyone sets
    monkeypatch.setattr(get_settings(), "smtp_host", "mail.example.org")
    monkeypatch.setattr(get_settings(), "smtp_from", "ostraka@example.org")
    assert (await client.get("/api/auth/forgot")).json() == {"available": False}
    monkeypatch.setattr(get_settings(), "public_url", "https://ostraka.example.org")
    assert (await client.post("/api/auth/forgot", json={"email": "a@example.org"})).status_code == 403


# ---- item 17: link addresses -------------------------------------------------------------------


async def test_api_links_use_the_page_origin_unless_the_public_url_is_set(
    db, client, make_person, sign_in, monkeypatch
):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    body = {"email": "new@example.org", "role": "responder", "team_id": await soc(db)}
    headers = {**admin.headers, "Origin": "https://ir.example.org:8443"}
    r = await client.post("/api/invitations", json=body, headers=headers)
    assert r.json()["link"].startswith("https://ir.example.org:8443/invite#")
    forged = {**admin.headers, "Origin": "javascript:alert(1)"}
    r = await client.post("/api/invitations", json=body, headers=forged)
    assert r.json()["link"].startswith("http://ostraka.test/invite#")

    monkeypatch.setattr(get_settings(), "public_url", "https://ostraka.example.org/")
    r = await client.post("/api/invitations", json=body, headers=headers)
    assert r.json()["link"].startswith("https://ostraka.example.org/invite#")


async def test_the_cli_warns_when_the_public_url_is_not_set(db, make_person, capsys, monkeypatch):
    assert await cli._bootstrap_admin("first@example.org", force=False, link_only=True) == 0
    out = capsys.readouterr()
    assert out.out.startswith("http://localhost:24747/invite#") and out.out.count("\n") == 1
    assert "OSTRAKA_PUBLIC_URL isn't set" in out.err

    await make_person("lost@example.org")
    assert await cli._reset_access("lost@example.org") == 0
    assert "OSTRAKA_PUBLIC_URL isn't set" in capsys.readouterr().err

    monkeypatch.setattr(get_settings(), "public_url", "https://ostraka.example.org")
    assert await cli._reset_access("lost@example.org") == 0
    out = capsys.readouterr()
    assert out.err == "" and "https://ostraka.example.org/invite#" in out.out


# ---- items 18 and 19: replaced links audited; deactivation revokes tokens ---------------------


async def test_a_replaced_invitation_is_recorded(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    await invite(client, db, admin)
    await invite(client, db, admin)
    revoked = await events(db, "invitation_revoked")
    assert [(e.actor_id, e.detail["reason"], e.detail["email"]) for e in revoked] == [
        (admin.id, "replaced", "new@example.org")
    ]


async def test_deactivating_an_account_revokes_its_api_tokens(db, client, make_person, sign_in):
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None))
    person = await sign_in(await make_person("leaver@example.org"))
    made = await client.post("/api/tokens", json={"name": "script"}, headers=person.headers)
    script = {"Authorization": f"Bearer {made.json()['token']}"}
    r = await client.patch(
        f"/api/users/{person.id}", json={"version": 1, "is_active": False}, headers=admin.headers
    )
    assert r.status_code == 200, r.text
    assert (await client.get("/api/users/me", headers=script)).status_code == 401
    changed = await events(db, "account_changed")
    assert changed[0].detail["tokens_revoked"] == 1
