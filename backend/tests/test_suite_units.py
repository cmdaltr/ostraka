"""The suite's pairing and hunt-run pieces that need no database."""

from types import SimpleNamespace

import pytest

from ostraka.api.shikari_runs import apply
from ostraka.integrations import pairing
from ostraka.models import Product
from ostraka.notify import channel_words

CODE = "pair_" + "A" * 43


def test_a_pairing_link_is_an_origin_and_a_code_after_the_hash():
    link = pairing.parse(f"  https://rowanholt.tail180683.ts.net/pair#{CODE}  ")
    assert link == pairing.Link("https://rowanholt.tail180683.ts.net", CODE)
    assert pairing.parse(f"https://shikari.example.org:8443/anything#{CODE}").origin == (
        "https://shikari.example.org:8443"
    )


@pytest.mark.parametrize(
    "link",
    [
        "https://rowanholt.example.org/pair",  # no code
        "https://rowanholt.example.org/pair#token_" + "A" * 43,  # not a pairing code
        "https://rowanholt.example.org/pair#pair_short",
        f"rowanholt.example.org/pair#{CODE}",  # no scheme
        f"ftp://rowanholt.example.org/pair#{CODE}",
    ],
)
def test_anything_else_is_not_a_pairing_link(link):
    with pytest.raises(pairing.PairingError):
        pairing.parse(link)


@pytest.mark.parametrize(
    "origin",
    [
        "http://rowanholt.example.org",  # plain http to the internet
        "https://169.254.169.254",  # cloud metadata
        "https://user:pw@rowanholt.example.org",
    ],
)
def test_pairing_refuses_the_addresses_a_connection_would(origin):
    with pytest.raises(pairing.PairingError):
        pairing.parse(f"{origin}/pair#{CODE}")


def test_plain_http_is_fine_on_the_organisations_own_network():
    assert pairing.parse(f"http://10.0.0.5:8000/pair#{CODE}").origin == "http://10.0.0.5:8000"


def test_a_health_answer_says_which_product_it_is():
    assert pairing.identify({"status": "ok", "app": "shikari"}, rowanholt=False) is Product.SHIKARI
    assert pairing.identify({"status": "healthy"}, rowanholt=True) is Product.ROWANHOLT
    assert pairing.identify({"status": "healthy"}, rowanholt=False) is None
    assert pairing.identify(["healthy"], rowanholt=True) is None


def run(**fields):
    base = dict(
        status="queued", group_name="", error="", detections=[], counts=[], totals={}, checked_at=None
    )
    return SimpleNamespace(**{**base, **fields})


def test_a_runs_snapshot_keeps_counts_and_detections_and_nothing_else():
    r = run()
    changed = apply(
        r,
        {
            "status": "executed",
            "group_name": "APT29",
            "detections": [
                {
                    "file": "T1059.001.yaml",
                    "name": "Encoded PowerShell",
                    "techniques": ["T1059.001"],
                    "platforms": ["splunk"],
                    "queries": [{"platform": "splunk", "query": "index=wineventlog EventCode=4104"}],
                    "results": [{"host": "WS-14"}],  # not part of the contract: dropped
                }
            ],
            "counts": [
                {"file": "T1059.001.yaml", "platform": "splunk", "hits": 12, "rows": [{"user": "cfo"}]},
                {"file": "T1003.001.yaml", "platform": "defender", "hits": True},  # not a count
                {"file": "T1003.001.yaml", "platform": "secops", "hits": -3},
                {"file": "T1021.001.yaml", "platform": "elastic", "skipped": True},
            ],
            "totals": {"queries": 4, "run": 3, "hits": 12, "label": "x"},
            "rows": [{"user": "cfo"}],
        },
    )
    assert changed and r.status == "executed" and r.group_name == "APT29"
    assert (
        "results" not in r.detections[0] and "cfo" not in str(r.counts) and "WS-14" not in str(r.detections)
    )
    assert [c["hits"] for c in r.counts] == [12, None, None, None]
    assert r.counts[3]["skipped"] is True
    assert r.totals == {"queries": 4, "run": 3, "hits": 12}
    assert apply(r, {"status": "executed"}) is False  # no change of status


def test_an_incident_channel_hears_when_suite_work_finishes():
    assert channel_words("rowanholt_job_finished", {}) == "A Rowanholt job finished"
    assert channel_words("shikari_run_updated", {"status": "executed"}) == "A Shikari hunt finished"
    assert channel_words("shikari_run_updated", {"status": "generating"}) is None
    assert channel_words("shikari_run_started", {}) is None
