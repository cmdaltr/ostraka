"""Pnyx's bearings and the checks on a model's answer: fixed rules on a snapshot, no database."""

from datetime import UTC, datetime, timedelta

from ostraka import pnyx
from ostraka.models import FindingStatus, IncidentRole, TaskStatus

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def facts(**overrides) -> pnyx.Facts:
    base = dict(
        ref="OST-26-007",
        title="Supplier invoice fraud",
        codename="Vivid Bench",
        kind="incident",
        phase="contain",
        severity="high",
        tlp="amber",
        closed=False,
        now=NOW,
        asker="Sam Soc",
        asker_role=IncidentRole.RESPONDER,
        commander="Lucía Ferreira",
    )
    return pnyx.Facts(**{**base, **overrides})


def task(ref, *, mine=False, queue=False, status=TaskStatus.OPEN, due=None, owner="Dana Lead"):
    return pnyx.TaskFact(ref, f"Task {ref}", owner, mine, queue, status, due)


def lead(ref, title="Same rule on another mailbox"):
    return pnyx.FindingFact(ref, title, FindingStatus.LEAD)


def answer(f: pnyx.Facts, key: str) -> list[str]:
    return next(a.lines for a in pnyx.bearings(f) if a.key == key)


def test_every_standing_question_is_answered_in_order():
    assert [a.key for a in pnyx.bearings(facts())] == [
        "now",
        "outstanding",
        "last_update",
        "concern",
        "leadership",
    ]


def test_what_to_do_now_is_your_tasks_by_deadline_then_your_teams_queue():
    f = facts(
        tasks=[
            task("TSK-3", mine=True, due=NOW + timedelta(hours=5)),
            task("TSK-1", mine=True, due=NOW - timedelta(hours=1)),
            task("TSK-9", queue=True),
            task("TSK-4"),
        ]
    )
    lines = answer(f, "now")
    assert lines[0].startswith("Your task: TSK-1") and "overdue since 18 Nov 11:00 UTC" in lines[0]
    assert lines[1].startswith("Your task: TSK-3")
    assert lines[2].startswith("Unclaimed in your team’s queue: TSK-9")
    assert not any("TSK-4" in line for line in lines)


def test_with_nothing_for_you_it_says_who_to_ask():
    assert answer(facts(), "now") == [
        "Nothing in OST-26-007 is assigned to you or waiting in your team’s queue. "
        "Ask Lucía Ferreira, who holds command."
    ]


def test_the_commander_is_told_what_command_owes():
    f = facts(
        asker_role=IncidentRole.COMMANDER,
        next_update_due=NOW - timedelta(minutes=30),
        update_rhythm="every 4 h",
        deadlines=[pnyx.DeadlineFact("CLK-2", "GDPR", "Notify the ICO", NOW + timedelta(hours=6))],
        tasks=[task("TSK-5", status=TaskStatus.BLOCKED)],
        findings=[lead("FND-3")],
    )
    lines = answer(f, "now")
    assert lines[0] == "The next stakeholder update is overdue since 18 Nov 11:30 UTC (every 4 h)."
    assert lines[1] == "Regulator deadline: CLK-2 GDPR: Notify the ICO, due 18 Nov 18:00 UTC."
    assert lines[2].startswith("Unblock: TSK-5")
    assert lines[3] == "Get FND-3 confirmed or refuted: leads decide the scope."


def test_the_biggest_concern_follows_the_fixed_order():
    f = facts(
        tasks=[task("TSK-2", due=NOW - timedelta(hours=2)), task("TSK-6", status=TaskStatus.BLOCKED)],
        deadlines=[
            pnyx.DeadlineFact("CLK-1", "NIS", "Early warning", NOW + timedelta(hours=3)),
            pnyx.DeadlineFact("CLK-1", "NIS", "Final report", NOW + timedelta(days=30)),
        ],
        findings=[lead("FND-8")],
    )
    lines = answer(f, "concern")
    assert lines[0] == "Regulator deadline: CLK-1 NIS: Early warning, due 18 Nov 15:00 UTC."
    assert lines[1].startswith("Also: Overdue task: TSK-2")
    assert lines[2].startswith("Also: Blocked task: TSK-6")
    assert len(lines) == 3


def test_a_quiet_incident_has_no_concern():
    assert answer(facts(), "concern") == [
        "Nothing is overdue, blocked, open as a lead or waiting on a regulator’s deadline."
    ]


def test_the_last_update_is_quoted_as_sent():
    f = facts(
        last_update=pnyx.UpdateFact("UPD-4", 2, NOW - timedelta(hours=3), ("Contained.", "No payment made.")),
        next_update_due=NOW + timedelta(hours=1),
        update_rhythm="every 4 h",
    )
    assert answer(f, "last_update") == [
        "UPD-4, update 2, sent 18 Nov 09:00 UTC:",
        "Contained.",
        "No payment made.",
        "The next stakeholder update is due 18 Nov 13:00 UTC (every 4 h).",
    ]
    assert answer(facts(), "last_update") == ["No stakeholder update has been sent for OST-26-007 yet."]


def test_leadership_hears_position_assessment_findings_and_the_main_concern():
    f = facts(
        assessment="ASM-2, it is highly likely (80–90%) that no payment left the business",
        findings=[pnyx.FindingFact("FND-1", "MFA fatigue", FindingStatus.CONFIRMED), lead("FND-3")],
    )
    lines = answer(f, "leadership")
    assert lines[0] == (
        "OST-26-007 Vivid Bench (Supplier invoice fraud) is open: incident in the contain phase, "
        "severity high, TLP:AMBER."
    )
    assert lines[1] == "Assessment: ASM-2, it is highly likely (80–90%) that no payment left the business."
    assert lines[2] == "1 confirmed finding (FND-1) and 1 lead still being worked (FND-3)."
    assert lines[3].startswith("Main concern: 1 lead still open")


def test_the_model_reads_the_record_with_indicators_masked():
    f = facts(findings=[lead("FND-3", "Beacon to 185.220.101.4")])
    text = pnyx.prompt(f, "What talks to 185.220.101.4?")
    assert "185.220.101.4" not in text
    assert "FND-3" in text and "What talks to" in text


def test_an_answer_citing_what_the_record_doesnt_hold_is_set_aside():
    f = facts(findings=[lead("FND-3")])
    assert pnyx.problems(f, "See FND-3.") == []
    assert pnyx.problems(f, "See FND-3 and TSK-99.") == ["It cites TSK-99, which the record doesn't hold."]


def test_an_answer_calling_a_lead_confirmed_is_set_aside():
    f = facts(findings=[lead("FND-3"), pnyx.FindingFact("FND-1", "MFA fatigue", FindingStatus.CONFIRMED)])
    assert pnyx.problems(f, "FND-3 is confirmed.") == ["It calls FND-3 confirmed, but it is still a lead."]
    assert pnyx.problems(f, "FND-1 is confirmed. FND-3 is not yet confirmed.") == []


def test_an_answer_asserting_certainty_is_set_aside():
    assert pnyx.problems(facts(), "It was definitely the supplier.") == [
        "It asserts certainty in words, which only the evidence may."
    ]
