"""The rules and primitives behind sign-in, without a database."""

import uuid

import pyotp
import pytest

from ostraka.auth import policy, tokens, totp
from ostraka.auth.secrets import (
    decrypt,
    encrypt,
    hash_password,
    new_backup_code,
    normalise_backup_code,
    password_problem,
    verify_password,
)
from ostraka.models import Role, User

SOC, DFIR = uuid.uuid4(), uuid.uuid4()


def person(role: Role, team=SOC) -> User:
    return User(id=uuid.uuid4(), role=role, team_id=team, email="x@example.org", name="X")


@pytest.mark.parametrize(
    ("actor", "role", "team", "allowed"),
    [
        (Role.ADMIN, Role.ADMIN, None, True),
        (Role.ADMIN, Role.TEAM_LEAD, DFIR, True),
        (Role.ADMIN, Role.RESPONDER, None, False),  # everyone but an admin needs a team
        (Role.TEAM_LEAD, Role.RESPONDER, SOC, True),
        (Role.TEAM_LEAD, Role.OBSERVER, SOC, True),
        (Role.TEAM_LEAD, Role.RESPONDER, DFIR, False),  # only their own team
        (Role.TEAM_LEAD, Role.TEAM_LEAD, SOC, False),  # only admins make leads
        (Role.TEAM_LEAD, Role.ADMIN, None, False),
        (Role.RESPONDER, Role.RESPONDER, SOC, False),
        (Role.OBSERVER, Role.OBSERVER, SOC, False),
    ],
)
def test_who_may_invite_whom(actor, role, team, allowed):
    assert (policy.can_invite(person(actor), role, team) is None) is allowed


def test_only_admins_change_accounts_and_never_their_own():
    admin, other = person(Role.ADMIN, None), person(Role.RESPONDER)
    assert policy.can_change_account(admin, other) is None
    assert policy.can_change_account(admin, admin) is not None
    assert policy.can_change_account(person(Role.TEAM_LEAD), other) is not None


def test_everyone_but_observers_sees_out_of_hours_phones():
    subject, observer = person(Role.RESPONDER), person(Role.OBSERVER)
    for viewer in (person(Role.ADMIN, None), person(Role.TEAM_LEAD, DFIR), person(Role.RESPONDER, DFIR)):
        assert policy.can_see_phone(viewer, subject)
    assert not policy.can_see_phone(observer, subject)
    assert policy.can_see_phone(observer, observer)  # their own


@pytest.mark.parametrize(
    ("role", "worked_on", "is_commander", "sensitive", "allowed"),
    [
        (Role.RESPONDER, True, False, False, True),  # worked on it
        (Role.RESPONDER, False, False, False, False),  # can see it, never touched it
        (Role.ADMIN, False, False, False, False),  # seeing everything isn't working on it
        (Role.RESPONDER, False, True, False, True),  # the commander
        (Role.RESPONDER, True, False, True, False),  # sensitive: worked on it isn't enough
        (Role.TEAM_LEAD, False, True, True, True),  # sensitive: the commander alone
        (Role.OBSERVER, True, False, False, False),
    ],
)
def test_who_may_export_an_incidents_reports(role, worked_on, is_commander, sensitive, allowed):
    why = policy.can_export(person(role), worked_on=worked_on, is_commander=is_commander, sensitive=sensitive)
    assert (why is None) is allowed


def test_team_leads_manage_only_their_teams_invitations():
    lead = person(Role.TEAM_LEAD)
    assert policy.can_manage_invitations_of(lead, SOC)
    assert not policy.can_manage_invitations_of(lead, DFIR)
    assert policy.can_manage_invitations_of(person(Role.ADMIN, None), DFIR)


@pytest.mark.parametrize(
    ("password", "ok"),
    [
        ("short", False),
        ("aaaaaaaaaaaaaaaa", False),
        ("analyst-password-123", False),  # contains the email's local part
        ("correct horse battery staple", True),
    ],
)
def test_password_rules(password, ok):
    assert (password_problem(password, "analyst@example.org", "Sam Analyst") is None) is ok


def test_password_hash_round_trip_and_unknown_user():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)
    assert not verify_password("anything", None)


def test_totp_secret_is_encrypted_at_rest():
    secret = pyotp.random_base32()
    sealed = encrypt(secret)
    assert secret not in sealed
    assert decrypt(sealed) == secret


def test_backup_codes_are_forgiving_to_type():
    code = new_backup_code()
    assert len(code) == 11 and code[5] == "-"
    assert normalise_backup_code(code.lower().replace("-", " ")) == code


def test_totp_accepts_drift_and_refuses_replay():
    secret = pyotp.random_base32()
    now = 1_800_000_000.0
    step = int(now // totp.STEP_SECONDS)
    code = pyotp.TOTP(secret).at(now)
    assert totp.matching_step(secret, code, 0, now=now) == step
    assert totp.matching_step(secret, code, step, now=now) is None  # same code again
    previous = pyotp.TOTP(secret).at(now - totp.STEP_SECONDS)
    assert totp.matching_step(secret, previous, 0, now=now) == step - 1
    stale = pyotp.TOTP(secret).at(now - 5 * totp.STEP_SECONDS)
    assert totp.matching_step(secret, stale, 0, now=now) is None


def test_mfa_ticket_is_not_an_access_token():
    user_id = uuid.uuid4()
    ticket = tokens.mfa_ticket(user_id)
    assert tokens.read_mfa_ticket(ticket) == user_id
    assert tokens.read_access_token(ticket) is None
    access = tokens.access_token(user_id, Role.RESPONDER, uuid.uuid4())
    assert tokens.read_mfa_ticket(access) is None
