"""SIEM queries without a database: who may connect and run, which settings each kind takes and which are
secret, building samarkand's client from them, and the search term as a pattern."""

import json

from samarkand import siem as samarkand_siem

from ostraka import siem
from ostraka.auth import policy
from ostraka.models import Role, SiemKind, User


def test_only_admins_connect_a_siem_and_observers_run_nothing():
    assert policy.can_manage_siems(User(role=Role.ADMIN)) is None
    for role in (Role.TEAM_LEAD, Role.RESPONDER, Role.OBSERVER):
        assert "Only admins" in policy.can_manage_siems(User(role=role))
    for role in (Role.ADMIN, Role.TEAM_LEAD, Role.RESPONDER):
        assert policy.can_run_queries(User(role=role)) is None
    assert "Observers" in policy.can_run_queries(User(role=Role.OBSERVER))


def test_a_form_splits_into_settings_and_secrets():
    settings, secrets = siem.split(
        SiemKind.SPLUNK,
        {"url": " https://splunk.example:8089 ", "token": "t0k", "password": "", "other": "x"},
    )
    assert settings == {"url": "https://splunk.example:8089"} and secrets == {"token": "t0k"}


def test_what_each_kind_needs_is_said_plainly():
    assert (
        siem.problem(SiemKind.DEFENDER, {"tenant_id": "t"}, {})
        == "Defender XDR needs its application (client) ID."
    )
    assert "token, or a username" in siem.problem(SiemKind.SPLUNK, {"url": "https://s:8089"}, {})
    assert (
        siem.problem(SiemKind.SPLUNK, {"url": "ftp://s"}, {"token": "t"})
        == "The URL must start with https://."
    )
    bad_key = {"project": "p", "instance": "i", "region": "eu"}
    assert "isn't JSON" in siem.problem(SiemKind.SECOPS, bad_key, {"service_account": "not json"})
    assert siem.problem(SiemKind.ELASTIC, {"url": "https://es:9200"}, {"api_key": "k"}) is None


def test_a_connection_makes_samarkands_client():
    splunk = siem.client(SiemKind.SPLUNK, {"url": "https://s:8089"}, {"token": "t"}, verify=False)
    assert isinstance(splunk, samarkand_siem.Splunk) and splunk.verify is False
    defender = siem.client(
        SiemKind.DEFENDER, {"tenant_id": "t", "client_id": "c"}, {"client_secret": "s"}, verify=True
    )
    assert isinstance(defender, samarkand_siem.Defender)
    account = {"client_email": "a@p.iam.gserviceaccount.com", "private_key": "-----BEGIN PRIVATE KEY-----"}
    secops = siem.client(
        SiemKind.SECOPS,
        {"project": "p", "instance": "i"},
        {"service_account": json.dumps(account)},
        verify=True,
    )
    assert isinstance(secops, samarkand_siem.SecOps) and secops.region == "us"


def test_every_kind_lists_a_secret_and_says_how_its_window_works():
    for kind, described in siem.KINDS.items():
        assert any(f.secret for f in described.fields), kind
        assert described.window and described.language


def test_a_search_term_is_matched_literally():
    assert siem.like("50%_off\\") == "%50\\%\\_off\\\\%"
