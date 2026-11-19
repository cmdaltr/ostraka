"""The attack flow from a snapshot: the anatomy's steps a record shows, what ATT&CK expects next and why,
the analyst's calls, and how far each step is covered. No database."""

from datetime import UTC, datetime

from ostraka import anatomy, flow
from ostraka.models import FindingStatus, StageCall
from ostraka.observables.detect import ObservableType

AT = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def finding(ref, status, *techniques, text=""):
    return flow.FindingFact(ref, f"Finding {ref}", status, tuple(techniques), text)


def stage(f: flow.Flow, key: str) -> flow.StageOut:
    return next(s for s in f.stages if s.key == key)


def keys(f: flow.Flow, layer: str | None = None) -> list[str]:
    return [s.key for s in f.stages if layer is None or s.layer == layer]


def test_the_steps_are_shikaris_anatomy_in_order():
    assert [s.key for s in flow.STAGES][:4] == ["exploit", "phishing", "credentials", "toolkit"]
    assert [s.name for s in flow.STAGES if s.column == "action"] == [a for _, a, _ in anatomy.ACTIONS]
    assert [s.name for s in flow.STAGES if s.column == "service"] == [
        "Cloud",
        "Messaging",
        "Code and storage",
        "Infrastructure",
    ]
    assert len(flow.BY_KEY) == len(flow.STAGES)


def test_a_technique_is_placed_by_its_prefix():
    assert flow.stages_of("T1566.001") == ["phishing"]
    assert flow.stages_of("t1003.001") == ["steals_logins"]
    assert flow.stages_of("T1021.007") == ["credentials", "moves"]
    assert flow.stages_of("T9999") == []


def test_seen_steps_carry_this_records_findings_and_techniques():
    f = flow.build(flow.Facts(findings=[finding("FND-1", FindingStatus.CONFIRMED, "T1566.001")]))
    phishing = stage(f, "phishing")
    assert phishing.layer == "seen" and phishing.coverage == "covered" and phishing.verb == "Phishes"
    assert phishing.techniques == [("T1566.001", "Spearphishing Attachment")]
    assert [x.ref for x in phishing.findings] == ["FND-1"]


def test_what_a_finding_names_is_placed_as_shikari_places_it():
    f = flow.build(
        flow.Facts(
            findings=[
                finding("FND-1", FindingStatus.LEAD, "T1566.002", text="Lure hosted on Dropbox"),
                finding("FND-2", FindingStatus.LEAD, text="Exploited CVE-2023-34362, then ran Mimikatz"),
                finding("FND-3", FindingStatus.LEAD, text="AnyDesk installed; staged to MEGA"),
            ]
        )
    )
    assert stage(f, "phishing").items == ["Dropbox"]  # the service it phished through
    assert stage(f, "exploit").items == ["CVE-2023-34362"]
    assert stage(f, "toolkit").items == ["Stealers: Mimikatz", "Remote access: AnyDesk"]
    assert stage(f, "storage").items == ["MEGA"]
    assert [x.ref for x in stage(f, "storage").findings] == ["FND-3"]


def test_ordinary_words_are_not_read_as_tools():
    f = flow.build(
        flow.Facts(findings=[finding("FND-1", FindingStatus.LEAD, text="The first responder ran net use")])
    )
    assert "toolkit" not in keys(f)


def test_malicious_addresses_are_infrastructure_and_services_their_own_step():
    f = flow.build(
        flow.Facts(
            observables=[
                flow.ObservableFact(ObservableType.IP_ADDRESS, "203.0.113.7"),
                flow.ObservableFact(ObservableType.DOMAIN, "evil.example"),
                flow.ObservableFact(ObservableType.URL, "https://mega.nz/file/x"),
            ]
        )
    )
    assert stage(f, "infrastructure").items == ["2 addresses"]
    assert stage(f, "infrastructure").coverage == "focused"
    assert stage(f, "storage").items == ["MEGA"]


def test_data_taken_out_makes_storage_an_upload():
    f = flow.build(flow.Facts(findings=[finding("FND-1", FindingStatus.LEAD, "T1567.002")]))
    assert stage(f, "storage").verb == "Uploads to"
    assert stage(f, "takes_out").layer == "seen"


def test_a_refuted_finding_places_nothing():
    f = flow.build(flow.Facts(findings=[finding("FND-1", FindingStatus.REFUTED, "T1566.001")]))
    assert "phishing" not in keys(f, "seen")


def test_expected_steps_come_from_groups_like_this_record_and_say_why():
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


def test_once_the_way_in_is_known_the_others_are_not_expected():
    f = flow.build(flow.Facts(findings=[finding("FND-1", FindingStatus.CONFIRMED, "T1566.001", "T1059.001")]))
    assert [k for k in keys(f) if flow.BY_KEY[k].column == "way"] == ["phishing"]


def test_with_nothing_seen_nothing_is_expected():
    f = flow.build(flow.Facts())
    assert f.stages == [] and f.peers == 0


def test_an_attributed_group_shapes_the_expectations():
    f = flow.build(flow.Facts(findings=[finding("FND-1", FindingStatus.LEAD, "T1566.001")], group="G0016"))
    assert f.group == ("G0016", "APT29") and f.peers == 1
    expected = [s for s in f.stages if s.layer == "expected"]
    assert expected and all(s.why == "APT29 (G0016) is known to do this." for s in expected)
    assert stage(f, "phishing").coverage == "focused"


def test_open_work_keeps_a_step_from_being_covered():
    facts = flow.Facts(
        findings=[finding("FND-1", FindingStatus.CONFIRMED, "T1566.001")],
        tasks=[flow.TaskFact("TSK-2", "Pull the mail logs", "phishing", done=False)],
    )
    assert stage(flow.build(facts), "phishing").coverage == "focused"
    facts.tasks = [flow.TaskFact("TSK-2", "Pull the mail logs", "phishing", done=True)]
    s = stage(flow.build(facts), "phishing")
    assert s.coverage == "covered" and s.tasks_done == 1


def test_a_playbooks_checks_draw_their_step_but_doing_them_is_not_an_answer():
    # A phish is known, so the other ways in aren't expected, but a playbook checking sign-ins draws one
    facts = flow.Facts(
        findings=[finding("FND-1", FindingStatus.CONFIRMED, "T1566.001")],
        tasks=[
            flow.TaskFact("TSK-3", "Review sign-ins", "credentials", done=False),
            flow.TaskFact("TSK-4", "Check backups are offline", "destroys", done=True),
        ],
    )
    f = flow.build(facts)
    assert stage(f, "credentials").layer == "expected" and stage(f, "credentials").why == flow.TASKS_WHY
    assert stage(f, "credentials").coverage == "focused"
    # Its checks done, the step still needs a finding confirmed, or checked and nothing found, naming where
    assert stage(f, "destroys").layer == "expected" and stage(f, "destroys").coverage == "open"


def test_an_analyst_clears_adds_or_dismisses_a_step():
    facts = flow.Facts(
        findings=[finding("FND-1", FindingStatus.CONFIRMED, "T1566.001")],
        calls=[
            flow.CallFact(
                "takes_out", StageCall.CLEARED, "Proxy and DLP logs, 10–14 Nov: nothing out", "Dee", AT
            ),
            flow.CallFact("destroys", StageCall.DISMISSED, "No encryption on any host", "Dee", AT),
            flow.CallFact(
                "credentials", StageCall.EXPECTED, "The same user's VPN account signed in", "Sam", AT
            ),
        ],
    )
    f = flow.build(facts)
    takes = stage(f, "takes_out")
    assert takes.coverage == "covered" and takes.call and takes.call.note.startswith("Proxy and DLP")
    assert "destroys" not in keys(f)
    assert [(s.key, c.note) for s, c in f.dismissed] == [("destroys", "No encryption on any host")]
    assert stage(f, "credentials").layer == "added"
    assert f.covered == 2
