"""The suite links without a database: addresses, the TLP guard, Shikari's signatures and coverage,
reading a Rowanholt row, and who may connect them. No live calls: httpx answers from a mock."""

import json

import httpx
import pytest

from ostraka.auth import policy
from ostraka.integrations import http, on_own_network, rowanholt, shikari, signing, tlp_refusal, url_problem
from ostraka.integrations.gaps import attack_url
from ostraka.models import Incident, Integration, Product, RecordKind, Role, Severity, Tlp, User


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://rowanholt.example.org", True),
        ("https://homelab.tail180683.ts.net:8443", True),
        ("http://127.0.0.1:5688", True),
        ("http://192.168.1.20", True),
        ("http://rowanholt.example.org", False),
        ("http://169.254.169.254", False),
        ("https://169.254.169.254", False),
        ("ftp://rowanholt.example.org", False),
        ("https://user:pass@rowanholt.example.org", False),
        ("https://rowanholt.example.org/?next=1", False),
    ],
)
def test_server_addresses(url, ok):
    assert (url_problem(url) is None) is ok


@pytest.mark.parametrize(
    ("url", "own"),
    [
        ("https://rowanholt.tail180683.ts.net", True),
        ("http://100.101.102.103:5688", True),
        ("http://10.0.0.5", True),
        ("http://localhost:5688", True),
        ("https://rowanholt.corp.internal", True),
        ("https://rowanholt.example.org", False),
        ("https://203.0.113.9", False),
    ],
)
def test_own_network(url, own):
    assert on_own_network(url) is own


def _incident(tlp: Tlp) -> Incident:
    return Incident(kind=RecordKind.INCIDENT, year=2026, number=7, title="t", severity=Severity.HIGH, tlp=tlp)


def test_tlp_guard_keeps_restricted_incidents_on_your_own_network():
    public = Integration(product=Product.ROWANHOLT, url="https://rowanholt.example.org")
    own = Integration(product=Product.ROWANHOLT, url="https://rowanholt.tail180683.ts.net")
    for tlp in (Tlp.RED, Tlp.AMBER_STRICT):
        refusal = tlp_refusal(Product.ROWANHOLT, _incident(tlp), public)
        assert refusal and "OST-2026-007" in refusal and "own network" in refusal
        assert tlp_refusal(Product.ROWANHOLT, _incident(tlp), own) is None
    for tlp in (Tlp.AMBER, Tlp.GREEN, Tlp.CLEAR):
        assert tlp_refusal(Product.SHIKARI, _incident(tlp), public) is None


def test_shikari_signature_is_hmac_sha256_of_the_raw_body():
    body = json.dumps({"event": "run.completed", "sent_at": 1.0, "data": {"run": "FIN7"}}).encode()
    good = shikari.sign("s3cret", body)
    assert good.startswith("sha256=") and len(good) == 7 + 64
    assert shikari.signature_ok("s3cret", body, good)
    assert not shikari.signature_ok("s3cret", body + b" ", good)
    assert not shikari.signature_ok("other", body, good)
    assert not shikari.signature_ok("s3cret", body, None)
    assert not shikari.signature_ok("", body, good)


def test_citation_is_written_as_aule_writes_it():
    assert rowanholt.cite("DC02", "cooked/evtx/security.jsonl", "18833", "a" * 64) == (
        "DC02: cooked/evtx/security.jsonl row 18833 (sha256 " + "a" * 16 + ")"
    )
    assert attack_url("T1003.001") == "https://attack.mitre.org/techniques/T1003/001/"


def test_only_admins_connect_servers():
    assert policy.can_manage_integrations(User(role=Role.ADMIN)) is None
    for role in (Role.TEAM_LEAD, Role.RESPONDER, Role.OBSERVER):
        assert "Only admins" in policy.can_manage_integrations(User(role=role))


@pytest.fixture
def answering(monkeypatch):
    seen: list[httpx.Request] = []

    def use(handler):
        def record(request):
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(record))
        return seen

    return use


async def test_coverage_asks_in_one_request_and_reads_covered(answering):
    seen = answering(
        lambda r: httpx.Response(
            200,
            json={
                "techniques": [
                    {"technique_id": "T1059.001", "name": "PowerShell", "covered": True},
                    {"technique_id": "T1003.001", "name": "LSASS Memory", "covered": False},
                ]
            },
        )
    )
    answer = await shikari.Shikari("https://shikari.example.org", "shik_x").coverage(
        ["t1059.001", "T1003.001"]
    )
    assert answer["T1059.001"].covered and not answer["T1003.001"].covered
    [request] = seen
    assert request.url.path == "/api/coverage"
    assert request.url.params["techniques"] == "T1003.001,T1059.001"
    assert request.headers["Authorization"] == "Bearer shik_x"


async def test_errors_say_unreachable_or_refused(answering):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    answering(down)
    with pytest.raises(http.Unreachable, match="can't be reached"):
        await shikari.Shikari("https://shikari.example.org", "t").health()
    answering(lambda r: httpx.Response(401, json={"error": "Invalid or revoked API token"}))
    with pytest.raises(http.Refused, match="refused the API token"):
        await shikari.Shikari("https://shikari.example.org", "t").health()
    answering(lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(http.Refused, match="JSON"):
        await rowanholt.Rowanholt("https://rowanholt.example.org", "").health()


async def test_a_row_is_found_by_reference_in_the_page_around_it(answering):
    rows = [{"ref": str(n), "hash": f"h{n}", "cells": {"n": str(n)}} for n in range(1, 400)]

    def page(request):
        offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
        return httpx.Response(
            200, json={"columns": ["n"], "rows": rows[offset : offset + limit], "total": 399}
        )

    seen = answering(page)
    found = await rowanholt.Rowanholt("https://rowanholt.example.org", "rvt_x").row(
        "J", "DC02", "a.jsonl", "250"
    )
    assert found["hash"] == "h250"
    assert seen[0].url.path == "/api/jobs/J/case/rows" and seen[0].url.params["host"] == "DC02"
    assert await rowanholt.Rowanholt("https://r.example.org", "").row("J", "DC02", "a.jsonl", "9999") is None


def test_signing_is_standard_library_only_and_reads_the_shared_envelope():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(signing.__file__).read_text())
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m.startswith("ostraka") for m in imported)
    assert signing.headers_for("rowanholt") == ("X-Rowanholt-Event", "X-Rowanholt-Signature")
    delivery = signing.parse(b'{"event": "run.completed", "sent_at": 1.5, "data": {"run": "FIN7"}}')
    assert delivery.event == "run.completed" and delivery.data == {"run": "FIN7"}
    for bad in (b"nope", b"[]", b'{"data": {}}'):
        with pytest.raises(signing.NotADelivery):
            signing.parse(bad)
