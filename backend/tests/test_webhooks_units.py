"""Webhooks without a database: which URLs are allowed, which activity raises which event, the
signature, and what a restricted incident's body may carry."""

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from ostraka import notify, webhooks
from ostraka.models import RecordKind, Tlp


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.org/ostraka",
        "https://10.0.0.5/hook",
        "http://localhost:8080/hook",
        "http://127.0.0.1:9000/hook",
        "http://192.168.1.20/hook",
        "http://10.1.2.3/hook",
        "http://soar.localhost/hook",
    ],
)
def test_https_anywhere_and_http_only_on_this_machine_or_a_private_network(url):
    assert webhooks.url_problem(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.org/ostraka",
        "http://203.0.113.9/hook",
        "https://169.254.169.254/latest/meta-data",
        "http://169.254.169.254/latest/meta-data",
        "ftp://hooks.example.org",
        "hooks.example.org/ostraka",
    ],
)
def test_other_urls_are_refused(url):
    assert webhooks.url_problem(url)


def test_events_must_be_ones_ostraka_sends():
    assert webhooks.events_problem(["incident.created", "task.assigned"]) is None
    assert webhooks.events_problem([])
    assert webhooks.events_problem(["incident.created", "run.completed"])


INCIDENT, HUNT = RecordKind.INCIDENT, RecordKind.HUNT


@pytest.mark.parametrize(
    ("action", "data", "kind", "events"),
    [
        ("declared", {}, INCIDENT, ["incident.created"]),
        ("declared", {}, HUNT, []),
        ("incident_updated", {"changes": {"phase": "contain"}}, INCIDENT, ["incident.status_changed"]),
        ("incident_updated", {"changes": {"status": "open"}}, INCIDENT, ["incident.status_changed"]),
        ("incident_updated", {"changes": {"status": "closed"}}, INCIDENT, ["incident.closed"]),
        ("incident_updated", {"changes": {"title": "x"}}, INCIDENT, []),
        ("incident_updated", {"changes": {"status": "closed"}}, HUNT, ["hunt.completed"]),
        ("incident_updated", {"changes": {"phase": "analyse"}}, HUNT, []),
        ("task_added", {}, INCIDENT, ["task.assigned"]),
        ("task_claimed", {}, INCIDENT, ["task.assigned"]),
        ("task_updated", {"reassigned": True}, INCIDENT, ["task.assigned"]),
        ("task_updated", {"reassigned": False}, INCIDENT, []),
        ("commented", {}, INCIDENT, []),
    ],
)
def test_activity_raises_these_events(action, data, kind, events):
    assert webhooks.events_for(action, data, kind) == events


def test_the_signature_is_an_hmac_of_the_raw_body():
    body = webhooks.body_of("incident.created", {"ref": "OST-2026-001"})
    expected = hmac.new(b"s3cret-s3cret-s3cret", body, hashlib.sha256).hexdigest()
    assert webhooks.sign("s3cret-s3cret-s3cret", body) == f"sha256={expected}"
    assert set(json.loads(body)) == {"event", "sent_at", "data"}


def incident(tlp: Tlp):
    return SimpleNamespace(ref="OST-2026-007", tlp=tlp)


@pytest.mark.parametrize("tlp", [Tlp.RED, Tlp.AMBER_STRICT])
def test_a_restricted_incident_is_named_by_its_reference_only(tlp):
    data = {"ref": "OST-2026-007", "title": "Board member's account", "task": {"title": "Reset MFA"}}
    assert notify.outward_data(incident(tlp), data) == {"ref": "OST-2026-007"}


def test_indicators_are_masked_all_the_way_down():
    data = {"title": "Beaconing to 203.0.113.9", "task": {"title": "Block 203.0.113.9"}, "seats": 3}
    out = notify.outward_data(incident(Tlp.AMBER), data)
    assert "203.0.113.9" not in json.dumps(out)
    assert out["title"] == "Beaconing to [ip address]" and out["seats"] == 3
