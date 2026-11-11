"""Stakeholder updates without a database: the rhythm, the templates, the update linter, the rephrase
check, who may prepare and approve, and the TLP guard."""

from datetime import UTC, datetime

import pytest

from ostraka.auth import policy
from ostraka.config import get_settings
from ostraka.models import FindingStatus, Incident, Probability, Role, Severity, Tlp, UpdatePlan, User
from ostraka.standard import rephrase
from ostraka.standard import update as standard
from ostraka.standard.assessment import BasisItem, Draft, sentences
from ostraka.updates import ai, rhythm

DRAFT = Draft(
    judgement="the actor staged the payroll export for theft",
    probability=Probability.HIGHLY_LIKELY,
    basis=(BasisItem("FND-2", "Payroll export staged", FindingStatus.CONFIRMED),),
)


def composition(**overrides) -> standard.Composition:
    base = {
        "assessment_ref": "ASM-1",
        "assessment_sentences": tuple(sentences(DRAFT)),
        "assessment_new": True,
        "changes": (
            standard.Change("lead", "FND-3", "Rclone seen on FIN-WS-07"),
            standard.Change("task_done", "TSK-4", "Reset the user's password"),
            standard.Change("confirmed", "FND-2", "Payroll export staged."),
            standard.Change("containment", "AST-1", "FIN-WS-07", "contained"),
        ),
        "position": standard.Position(
            "contain", "high", assets=2, contained=1, tasks_open=3, tasks_blocked=1
        ),
        "next_line": rhythm.next_line(240),
    }
    return standard.Composition(**{**base, **overrides})


# ---- rhythm ------------------------------------------------------------------------------------------


def test_the_rhythm_is_set_by_severity_and_can_be_changed_per_incident():
    assert rhythm.every(Severity.CRITICAL, None) == 120
    assert rhythm.every(Severity.HIGH, None) == 240
    assert rhythm.every(Severity.MEDIUM, None) == 1440
    assert rhythm.every(Severity.LOW, None) is None
    assert rhythm.every(Severity.LOW, UpdatePlan(rhythm_set=True, every_minutes=60)) == 60
    assert rhythm.every(Severity.CRITICAL, UpdatePlan(rhythm_set=True, every_minutes=None)) is None
    assert rhythm.every(Severity.HIGH, UpdatePlan(rhythm_set=False, every_minutes=60)) == 240
    start = datetime(2026, 11, 11, 9, tzinfo=UTC)
    assert rhythm.next_due(120, start) == datetime(2026, 11, 11, 11, tzinfo=UTC)
    assert rhythm.next_due(None, start) is None
    assert [rhythm.words(m) for m in (120, 30, 1440, None)] == [
        "every 2 h",
        "every 30 min",
        "daily",
        "on change only",
    ]
    assert rhythm.next_line(1440) == "Next update: within 24 h."
    assert rhythm.next_line(None) == "Next update: when something material changes."


# ---- the draft ---------------------------------------------------------------------------------------


def test_the_draft_leads_with_the_assessment_and_orders_changes_strongest_first():
    lines = standard.compose(composition())
    assert [line.section for line in lines] == [
        "assessment",
        "assessment",
        "what",
        "what",
        "what",
        "what",
        "position",
        "next",
    ]
    assert lines[0].text.startswith("We assess it is highly likely (80–90%) that the actor staged")
    whats = [line.text for line in lines if line.section == "what"]
    assert whats == [
        "Confirmed: FND-2 Payroll export staged.",
        "AST-1 FIN-WS-07 is now contained.",
        "Done: TSK-4 Reset the user's password.",
        "Lead, not yet confirmed: FND-3 Rclone seen on FIN-WS-07.",
    ]
    assert lines[-2].text == (
        "Where things stand: the response is in the contain phase at high severity; "
        "1 of 2 affected assets contained; 3 tasks open, 1 blocked."
    )
    assert lines[-1].text == "Next update: within 4 h."


def test_a_quiet_period_says_so_and_an_unchanged_assessment_says_since_when():
    lines = standard.compose(
        composition(changes=(), assessment_new=False, previous="UPD-3", note="Board call at 5.")
    )
    texts = [line.text for line in lines]
    assert "The assessment is unchanged since UPD-3." in texts
    assert "Nothing material has changed since UPD-3." in texts
    assert [line for line in lines if line.section == "note"][0].text == "Board call at 5."


def test_an_update_without_an_assessment_cant_be_approved():
    c = composition(assessment_ref=None, assessment_sentences=())
    check = standard.check(c, standard.compose(c), None)
    assert [f.rule for f in check.errors] == ["no-assessment"]
    assert not standard.check(composition(), standard.compose(composition()), DRAFT).errors


def test_the_linter_carries_the_assessments_own_errors_and_checks_comms_words():
    leads_only = Draft(
        judgement="the actor staged the payroll export",
        probability=Probability.ALMOST_CERTAIN,
        basis=(BasisItem("FND-3", "Rclone seen", FindingStatus.LEAD),),
    )
    c = composition(note="This is clearly a huge breach and they probably took everything.")
    check = standard.check(c, standard.compose(c), leads_only, superseded=True)
    rules = {(f.rule, f.severity, f.field) for f in check.flags}
    assert ("unbacked-certainty", "error", "assessment") in rules
    assert ("superseded-assessment", "error", "assessment") in rules
    assert ("certainty-words", "warning", "note") in rules
    assert ("loaded-adjective", "warning", "note") in rules
    assert ("probability-in-text", "warning", "note") in rules


def test_more_than_four_changes_is_a_four_three_three_warning():
    many = tuple(standard.Change("task_done", f"TSK-{i}", "Something done") for i in range(5))
    c = composition(changes=many)
    assert "four-three-three" in {f.rule for f in standard.check(c, standard.compose(c), DRAFT).flags}


def test_a_stale_rewording_cant_go_out():
    c = composition()
    lines = standard.compose(c)
    check = standard.check(c, lines, DRAFT, rephrased=["only one line"], use_rephrased=True)
    assert [f.rule for f in check.errors] == ["stale-rephrase"]


def test_the_plain_text_has_a_heading_per_section():
    lines = standard.compose(composition(note="Board call at 5."))
    text = standard.render(lines)
    assert text.startswith("Assessment\nWe assess it is highly likely")
    assert "What has changed\n- Confirmed: FND-2 Payroll export staged.\n- AST-1" in text
    assert "From the communications lead\nBoard call at 5." in text
    assert text.endswith("Next update: within 4 h.")


# ---- the rephrase check ------------------------------------------------------------------------------

ORIGINAL = [
    "We assess it is highly likely (80–90%) that the actor staged the payroll export for theft.",
    "Lead, not yet confirmed: FND-3 Rclone seen on [ip address].",
    "There is no evidence of data leaving the network: a Defender XDR search of FIN-WS-07 found nothing.",
]


def test_a_rewording_that_keeps_every_claim_is_accepted():
    reworded = [
        "We assess it is highly likely (80–90%) that the actor gathered the payroll export to steal it.",
        "A lead that isn't confirmed yet: FND-3, Rclone on [ip address].",
        "There is no evidence of data leaving the network; Defender XDR found nothing "
        "when it searched FIN-WS-07.",
    ]
    assert rephrase.problems(ORIGINAL, reworded) == []


@pytest.mark.parametrize(
    ("line", "changed", "why"),
    [
        (
            0,
            "We assess it is likely (55–75%) that the actor staged the payroll export for theft.",
            "probability",
        ),
        (0, "We assess the actor staged the payroll export for theft.", "probability"),
        (1, "Confirmed: FND-3 Rclone seen on [ip address].", "strongly"),
        (1, "Lead, not yet confirmed: Rclone seen on [ip address].", "references"),
        (1, "Lead, not yet confirmed: FND-3 Rclone seen on 203.0.113.9.", "numbers"),
        (2, "Data probably didn't leave: a Defender XDR search of FIN-WS-07 found nothing.", "strongly"),
        (
            0,
            "We assess it is highly likely (80–90%) that the actor definitely staged the export for theft.",
            "certainty",
        ),
    ],
)
def test_a_rewording_that_changes_a_claim_citation_or_term_is_rejected(line, changed, why):
    reworded = list(ORIGINAL)
    reworded[line] = changed
    found = rephrase.problems(ORIGINAL, reworded)
    assert found and any(why in p for p in found), found


def test_a_rewording_with_lines_missing_or_added_is_rejected():
    assert rephrase.problems(ORIGINAL, ORIGINAL[:2])
    assert rephrase.problems(ORIGINAL, [*ORIGINAL, "And another thing."])
    assert rephrase.problems(ORIGINAL, [ORIGINAL[0], "", ORIGINAL[2]])


def test_the_reply_is_read_as_a_json_array():
    assert ai.parse('Here you go:\n["one", "two"]') == ["one", "two"]
    assert ai.parse("no array") is None
    assert ai.parse("[1, 2]") is None


# ---- who may ---------------------------------------------------------------------------------------


def person(role: Role = Role.RESPONDER) -> User:
    return User(name="Someone", email="someone@example.org", role=role)


def test_comms_and_the_commander_prepare_updates_but_observers_dont():
    assert policy.can_run_updates(person(), is_commander=False, is_comms=True) is None
    assert policy.can_run_updates(person(), is_commander=True, is_comms=False) is None
    assert policy.can_run_updates(person(), is_commander=False, is_comms=False)
    assert policy.can_run_updates(person(Role.OBSERVER), is_commander=False, is_comms=True)


def test_comms_approves_and_the_commander_only_stands_in_when_nobody_holds_comms():
    assert policy.can_approve_update(person(), is_commander=False, is_comms=True, comms_named=True) is None
    assert policy.can_approve_update(person(), is_commander=True, is_comms=False, comms_named=False) is None
    assert "comms lead" in policy.can_approve_update(
        person(), is_commander=True, is_comms=False, comms_named=True
    )
    assert policy.can_approve_update(person(), is_commander=False, is_comms=False, comms_named=False)
    assert policy.can_approve_update(
        person(Role.OBSERVER), is_commander=False, is_comms=True, comms_named=True
    )


# ---- the TLP guard -----------------------------------------------------------------------------------


def incident(tlp: Tlp) -> Incident:
    return Incident(kind="incident", year=2026, number=1, title="t", severity=Severity.HIGH, tlp=tlp)


def test_restricted_incidents_only_ever_use_local_ollama(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(settings, "anthropic_model", "a-model")
    monkeypatch.setattr(settings, "ollama_model", "")
    assert ai.provider_for(incident(Tlp.AMBER)) == "anthropic"
    for tlp in (Tlp.RED, Tlp.AMBER_STRICT):
        with pytest.raises(ai.Unavailable, match="only a local Ollama model"):
            ai.provider_for(incident(tlp))
    monkeypatch.setattr(settings, "ollama_model", "qwen2.5")
    assert ai.provider_for(incident(Tlp.RED)) == "ollama"
    monkeypatch.setattr(settings, "ai_provider", "none")
    with pytest.raises(ai.Unavailable, match="switched off"):
        ai.provider_for(incident(Tlp.CLEAR))
