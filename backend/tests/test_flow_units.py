"""The attack flow from a snapshot: what's seen, what ATT&CK expects next and why, the analyst's calls,
and how far each stage is covered. No database."""

from datetime import UTC, datetime

from ostraka import flow
from ostraka.models import FindingStatus, StageCall

AT = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def finding(ref, status, *techniques):
    return flow.FindingFact(ref, f"Finding {ref}", status, tuple(techniques))


def stage(f: flow.Flow, key: str) -> flow.StageOut:
    return next(s for s in f.stages if s.key == key)


def test_stages_cover_every_attck_tactic():
    tactics = {t for t, _ in flow.attack.TACTICS}
    grouped = {t for s in flow.STAGES for t in s.tactics}
    assert tactics <= grouped


def test_a_technique_is_placed_by_its_tactics():
    assert flow.stages_of("T1566.001") == ["gets_in"]
    assert flow.stages_of("T1078") == ["gets_in", "stays", "hides"]
    assert flow.stages_of("T9999") == []


def test_seen_stages_carry_this_records_findings_and_techniques():
    f = flow.build(flow.Facts(findings=[finding("FND-1", FindingStatus.CONFIRMED, "T1566.001")]))
    gets_in = stage(f, "gets_in")
    assert gets_in.layer == "seen" and gets_in.coverage == "covered"
    assert gets_in.techniques == [("T1566.001", "Spearphishing Attachment")]
    assert [x.ref for x in gets_in.findings] == ["FND-1"]


def test_a_refuted_finding_places_nothing():
    f = flow.build(flow.Facts(findings=[finding("FND-1", FindingStatus.REFUTED, "T1566.001")]))
    assert all(s.key != "gets_in" or s.layer != "seen" for s in f.stages)


def test_expected_stages_come_from_groups_like_this_record_and_say_why():
    f = flow.build(flow.Facts(findings=[finding("FND-1", FindingStatus.CONFIRMED, "T1566.001", "T1059.001")]))
    expected = [s for s in f.stages if s.layer == "expected"]
    assert expected, "phishing then PowerShell should suggest what usually follows"
    assert f.peers == flow.PEERS
    for s in expected:
        assert s.coverage == "open"
        assert f"of the {flow.PEERS} ATT&CK groups most like this record" in s.why
        assert s.suggestions and all(x.groups > 0 for x in s.suggestions)
        # suggestions are techniques this record hasn't shown
        assert all(flow.base(x.technique) not in {"T1566", "T1059"} for x in s.suggestions)
    assert "prepares" not in [s.key for s in expected]


def test_with_nothing_seen_nothing_is_expected():
    f = flow.build(flow.Facts())
    assert f.stages == [] and f.peers == 0


def test_an_attributed_group_shapes_the_expectations():
    f = flow.build(flow.Facts(findings=[finding("FND-1", FindingStatus.LEAD, "T1566.001")], group="G0016"))
    assert f.group == ("G0016", "APT29") and f.peers == 1
    expected = [s for s in f.stages if s.layer == "expected"]
    assert expected and all(s.why == "APT29 (G0016) is known to reach this stage." for s in expected)
    assert stage(f, "gets_in").coverage == "focused"


def test_open_work_keeps_a_stage_from_being_covered():
    facts = flow.Facts(
        findings=[finding("FND-1", FindingStatus.CONFIRMED, "T1566.001")],
        tasks=[flow.TaskFact("TSK-2", "Pull the mail logs", "gets_in", done=False)],
    )
    assert stage(flow.build(facts), "gets_in").coverage == "focused"
    facts.tasks = [flow.TaskFact("TSK-2", "Pull the mail logs", "gets_in", done=True)]
    s = stage(flow.build(facts), "gets_in")
    assert s.coverage == "covered" and s.tasks_done == 1


def test_an_analyst_clears_adds_or_dismisses_a_stage():
    facts = flow.Facts(
        findings=[finding("FND-1", FindingStatus.CONFIRMED, "T1566.001")],
        calls=[
            flow.CallFact(
                "takes", StageCall.CLEARED, "Proxy and DLP logs, 10–14 Nov: nothing out", "Dee", AT
            ),
            flow.CallFact("impact", StageCall.DISMISSED, "No encryption on any host", "Dee", AT),
            flow.CallFact("prepares", StageCall.EXPECTED, "Lookalike domain registered last week", "Sam", AT),
        ],
    )
    f = flow.build(facts)
    takes = stage(f, "takes")
    assert takes.coverage == "covered" and takes.call and takes.call.note.startswith("Proxy and DLP")
    assert "impact" not in [s.key for s in f.stages]
    assert [(s.key, c.note) for s, c in f.dismissed] == [("impact", "No encryption on any host")]
    assert stage(f, "prepares").layer == "added"
    assert f.covered == 2
