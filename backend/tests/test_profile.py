"""The profile page against Postgres: profile fields and who sees a phone, signed-in browsers, your
own security log, changing authenticator, and the new-device alert."""

import time

import pyotp
import pytest
from sqlalchemy import select, update

from ostraka import notify
from ostraka.config import get_settings
from ostraka.models import Notification, NotificationKind, Role, User
from tests.conftest import PASSWORD

pytestmark = pytest.mark.db

FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0"


async def sign_in_as(client, person, agent: str) -> str:
    """Sign in from a named browser; returns the access token."""
    headers = {"User-Agent": agent}
    r = await client.post(
        "/api/auth/login", json={"email": person.user.email, "password": PASSWORD}, headers=headers
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/auth/mfa", json={"ticket": r.json()["ticket"], "code": person.code()}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def rewind(db, person) -> None:
    """Forget which authenticator codes were used, so one test can sign in more than twice a window."""
    await db.execute(update(User).where(User.id == person.user.id).values(totp_last_step=0))
    await db.commit()
    person.last_step = 0


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_the_profile_saves_and_checks_what_it_is_given(db, client, make_person, sign_in):
    sam = await sign_in(await make_person("sam@example.org", Role.RESPONDER, "SOC", name="Sam"))
    r = await client.patch(
        "/api/users/me",
        json={
            "job_title": " Senior analyst ",
            "phone": "+44 7700 900123",
            "timezone": "Europe/Paris",
            "availability": "busy",
            "quiet_from": "22:00",
            "quiet_until": "07:00",
            "muted_kinds": ["mention", "reply"],
        },
        headers=sam.headers,
    )
    assert r.status_code == 200, r.text
    me = r.json()
    assert (
        me["job_title"] == "Senior analyst"
        and me["availability"] == "busy"
        and me["timezone"] == "Europe/Paris"
    )
    assert me["quiet_from"] == "22:00" and me["muted_kinds"] == ["mention", "reply"]

    r = await client.patch("/api/users/me", json={"quiet_from": "", "quiet_until": ""}, headers=sam.headers)
    assert r.json()["quiet_from"] is None and r.json()["quiet_until"] is None
    for bad in (
        {"timezone": "Mars/Olympus_Mons"},
        {"phone": "call me"},
        {"quiet_from": "25:00"},
        {"muted_kinds": ["new_device"]},
        {"quiet_from": "22:00"},  # one end only
    ):
        r = await client.patch("/api/users/me", json=bad, headers=sam.headers)
        assert r.status_code == 422, bad


async def test_observers_see_availability_but_not_phones(db, client, make_person, sign_in):
    sam = await sign_in(await make_person("sam@example.org", Role.RESPONDER, "SOC", name="Sam"))
    await client.patch(
        "/api/users/me", json={"phone": "+44 7700 900123", "availability": "away"}, headers=sam.headers
    )
    dee = await sign_in(await make_person("dee@example.org", Role.RESPONDER, "Forensics", name="Dee"))
    obs = await sign_in(await make_person("obs@example.org", Role.OBSERVER, "SOC", name="Obs"))

    def sam_seen_by(person) -> dict:
        return next(u for u in r_people[person] if u["name"] == "Sam")

    r_people = {}
    for person in (dee, obs):
        r = await client.get("/api/users", headers=person.headers)
        r_people[person] = r.json()
    assert sam_seen_by(dee)["phone"] == "+44 7700 900123"
    assert sam_seen_by(obs)["phone"] is None
    assert sam_seen_by(obs)["availability"] == "away"


async def test_signed_in_browsers_are_listed_and_can_be_signed_out(db, client, make_person):
    person = await make_person("sam@example.org", Role.RESPONDER, "SOC", name="Sam")
    laptop = await sign_in_as(client, person, FIREFOX)
    client.cookies.clear()  # a second browser has its own cookie
    phone = await sign_in_as(
        client, person, "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) Safari/604.1"
    )

    r = await client.get("/api/users/me/sessions", headers=bearer(laptop))
    listed = r.json()
    assert len(listed) == 2 and {s["device"] for s in listed} == {"Firefox on macOS", "Safari on iPhone"}
    here = next(s for s in listed if s["current"])
    there = next(s for s in listed if not s["current"])
    assert here["device"] == "Firefox on macOS"

    r = await client.delete(f"/api/users/me/sessions/{here['family']}", headers=bearer(laptop))
    assert r.status_code == 409  # this browser signs out with Sign out
    r = await client.delete(f"/api/users/me/sessions/{there['family']}", headers=bearer(laptop))
    assert r.status_code == 204
    assert (await client.get("/api/users/me", headers=bearer(phone))).status_code == 401
    assert len((await client.get("/api/users/me/sessions", headers=bearer(laptop))).json()) == 1


async def test_your_security_log_shows_your_events_and_an_admins_changes(db, client, make_person, sign_in):
    sam = await sign_in(await make_person("sam@example.org", Role.RESPONDER, "SOC", name="Sam"))
    admin = await sign_in(await make_person("admin@example.org", Role.ADMIN, None, name="Ada"))
    r = await client.patch(
        f"/api/users/{sam.user.id}", json={"version": 1, "role": "team_lead"}, headers=admin.headers
    )
    assert r.status_code == 200, r.text
    r = await client.post("/api/auth/login", json={"email": "sam@example.org", "password": "wrong password!"})

    log = (await client.get("/api/users/me/security-log", headers=sam.headers)).json()
    actions = [e["action"] for e in log]
    assert "login" in actions and "login_failed" in actions
    changed = next(e for e in log if e["by"] == "Ada")
    assert changed["action"] != "login"
    assert all(e["by"] in (None, "Ada") for e in log)


async def test_a_new_authenticator_replaces_the_old_one_only_once_it_works(db, client, make_person, sign_in):
    sam = await sign_in(await make_person("sam@example.org", Role.RESPONDER, "SOC", name="Sam"))
    r = await client.post(
        "/api/users/me/authenticator",
        json={"password": "not it at all", "code": "123456"},
        headers=sam.headers,
    )
    assert r.status_code == 403
    r = await client.post(
        "/api/users/me/authenticator", json={"password": PASSWORD, "code": "000000"}, headers=sam.headers
    )
    assert r.status_code == 403
    r = await client.post(
        "/api/users/me/authenticator", json={"password": PASSWORD, "code": sam.code()}, headers=sam.headers
    )
    assert r.status_code == 200, r.text
    new_secret = r.json()["secret"]
    assert r.json()["qr_svg"].startswith("data:image/svg+xml")

    r = await client.post("/api/users/me/authenticator/confirm", json={"code": "000000"}, headers=sam.headers)
    assert r.status_code == 403
    r = await client.post(
        "/api/users/me/authenticator/confirm",
        json={"code": pyotp.TOTP(new_secret).now()},
        headers=sam.headers,
    )
    assert r.status_code == 204, r.text

    client.cookies.clear()
    r = await client.post("/api/auth/login", json={"email": "sam@example.org", "password": PASSWORD})
    ticket = r.json()["ticket"]
    # The next window's code: the current one was spent confirming, and a replay is refused
    next_code = pyotp.TOTP(new_secret).at(int(time.time()) + 30)
    r = await client.post("/api/auth/mfa", json={"ticket": ticket, "code": next_code})
    assert r.status_code == 200, r.text
    log = (await client.get("/api/users/me/security-log", headers=bearer(r.json()["access_token"]))).json()
    assert any(e["action"] == "mfa_changed" for e in log)


async def test_a_sign_in_from_a_new_browser_is_announced(db, client, make_person, outbox, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_host", "mail.example.org")
    monkeypatch.setattr(get_settings(), "smtp_from", "ostraka@example.org")
    person = await make_person("sam@example.org", Role.RESPONDER, "SOC", name="Sam")
    await sign_in_as(client, person, FIREFOX)  # the first sign-in is expected
    client.cookies.clear()
    await sign_in_as(client, person, FIREFOX)  # the same browser again
    client.cookies.clear()
    await rewind(db, person)
    await sign_in_as(client, person, FIREFOX.replace("132.0", "133.0"))  # the same browser, updated
    await notify.wait()
    assert not (await db.scalars(select(Notification).where(Notification.user_id == person.user.id))).all()

    client.cookies.clear()
    await rewind(db, person)
    await sign_in_as(client, person, "curl/8.7.1")
    await notify.wait()
    alerts = (await db.scalars(select(Notification).where(Notification.user_id == person.user.id))).all()
    assert [a.kind for a in alerts] == [NotificationKind.NEW_DEVICE]
    assert "curl/8.7.1" in alerts[0].title
    assert [m["To"] for m in outbox.emails] == ["sam@example.org"]
    assert "New sign-in" in outbox.emails[0]["Subject"]
