"""The reporting standard's fixed rules: the yardstick, the assessment linter and sentences, and the
ACH arithmetic."""

import uuid
from datetime import UTC, datetime

from ostraka.models import FindingStatus, Probability, Rating, Weight
from ostraka.standard import ach, yardstick
from ostraka.standard.assessment import Absence, BasisItem, Draft, lint, sentences

CONFIRMED = BasisItem("FND-12", "Payroll export staged in C:\\\\Temp", FindingStatus.CONFIRMED)
LEAD = BasisItem("FND-14", "Rclone seen on FIN-WS-07", FindingStatus.LEAD)
REFUTED = BasisItem("FND-9", "Beacon to 203.0.113.9", FindingStatus.REFUTED)
NO_EGRESS = Absence(
    "data leaving the network",
    "Defender XDR",
    "FIN-WS-07 network events",
    "DeviceNetworkEvents | where DeviceName == 'fin-ws-07'",
    datetime(2026, 11, 3, 0, 0, tzinfo=UTC),
    datetime(2026, 11, 4, 0, 0, tzinfo=UTC),
)


def rules(draft: Draft) -> dict[str, str]:
    return {f.rule: f.severity for f in lint(draft)}


def test_the_yardstick_is_the_phia_list_in_order():
    assert [yardstick.range_of(p) for p in yardstick.TERMS] == [
        "≤5%", "10–20%", "25–35%", "40–<50%", "55–75%", "80–90%", "≥95%",
    ]  # fmt: skip
    assert yardstick.phrase(Probability.HIGHLY_LIKELY, "x") == "it is highly likely (80–90%) that x"
    assert yardstick.phrase(Probability.REMOTE_CHANCE, "x") == "there is a remote chance (≤5%) that x"
    assert yardstick.phrase(Probability.LIKELY, "x") == "it is likely (55–75%) that x"


def test_ostraka_writes_the_assessment_bottom_line_first_strongest_evidence_first():
    draft = Draft(
        "the actor staged the payroll export for theft.",
        Probability.LIKELY,
        basis=(LEAD, CONFIRMED),
        absences=(NO_EGRESS,),
        so_what="Payroll data may need notifying to the ICO.",
    )
    assert sentences(draft) == [
        "We assess it is likely (55–75%) that the actor staged the payroll export for theft.",
        "This is based on FND-12 (confirmed: Payroll export staged in C:\\\\Temp) and "
        "FND-14 (lead: Rclone seen on FIN-WS-07).",
        "There is no evidence of data leaving the network: a Defender XDR search of FIN-WS-07 network "
        "events between 03 Nov 2026 00:00 UTC and 04 Nov 2026 00:00 UTC found nothing.",
        "So what: Payroll data may need notifying to the ICO.",
    ]


def test_an_assessment_needs_a_basis_and_never_a_refuted_one():
    assert rules(Draft("x happened", Probability.LIKELY))["no-basis"] == "error"
    assert (
        rules(Draft("x happened", Probability.LIKELY, basis=(REFUTED, CONFIRMED)))["refuted-basis"] == "error"
    )
    assert rules(Draft("x happened", Probability.UNLIKELY, absences=(NO_EGRESS,))) == {}


def test_certainty_has_to_be_earned_by_a_confirmed_finding():
    assert rules(Draft("x", Probability.ALMOST_CERTAIN, basis=(LEAD,)))["unbacked-certainty"] == "error"
    assert rules(Draft("x", Probability.LIKELY, basis=(LEAD,)))["leads-only"] == "warning"
    assert rules(Draft("x", Probability.ALMOST_CERTAIN, basis=(CONFIRMED, LEAD))) == {}
    assert (
        rules(Draft("x", Probability.HIGHLY_LIKELY, absences=(NO_EGRESS,)))["unbacked-certainty"] == "warning"
    )


def test_the_wording_is_checked_without_a_model():
    flags = {
        f.rule: f
        for f in lint(
            Draft(
                "We assess the actor clearly and probably ran a massive exfiltration",
                Probability.LIKELY,
                basis=(CONFIRMED,),
                what_next="This is definitely a huge problem.",
            )
        )
    }
    assert set(flags) == {"self-framed", "probability-in-text", "certainty-words", "loaded-adjective"}
    assert flags["probability-in-text"].words == ["probably"]
    loaded = [f for f in lint(Draft("a lacklustre response", Probability.LIKELY, basis=(CONFIRMED,)))]
    assert [(f.rule, f.words) for f in loaded] == [("loaded-adjective", ["lacklustre"])]


H1, H2, H3 = (uuid.uuid4() for _ in range(3))
E1, E2, E3 = (uuid.uuid4() for _ in range(3))
ANA, BEN = uuid.uuid4(), uuid.uuid4()
EVIDENCE = [
    ach.Evidence(E1, Weight.HIGH, Weight.HIGH),  # weight 1.414² ≈ 2.0
    ach.Evidence(E2, Weight.MEDIUM, Weight.MEDIUM),  # 1
    ach.Evidence(E3, Weight.LOW, Weight.MEDIUM),  # 0.707
]
C, X = Rating.CONSISTENT, Rating.INCONSISTENT


def test_ach_scores_weighted_inconsistency_and_finds_the_leader():
    ratings = {
        (H1, E1): {ANA: C}, (H2, E1): {ANA: X}, (H3, E1): {ANA: C},
        (H1, E2): {ANA: X}, (H2, E2): {ANA: C}, (H3, E2): {ANA: C},
        (H1, E3): {ANA: C}, (H2, E3): {ANA: C}, (H3, E3): {ANA: C},
    }  # fmt: skip
    result = ach.analyse([H1, H2, H3], [H1, H2, H3], EVIDENCE, ratings)
    assert result.scores == {H1: -1.0, H2: -1.999, H3: 0.0}
    assert result.leading == H3
    assert result.non_diagnostic == [E3]  # consistent with everything: proves nothing
    # Without E1 or E2, H3 would only tie for the lead
    assert result.pivotal == {E1: None, E2: None}
    assert "1.414" in result.algorithm


def test_a_rejected_hypothesis_is_scored_but_cannot_lead():
    ratings = {(H1, E1): {ANA: C}, (H2, E1): {ANA: X}}
    result = ach.analyse([H1, H2], [H2], EVIDENCE, ratings)
    assert result.scores[H1] == 0.0 and result.leading == H2


def test_ach_averages_analysts_flags_disagreement_and_sensitivity():
    ratings = {
        (H1, E1): {ANA: X, BEN: X}, (H2, E1): {ANA: C, BEN: C},
        (H1, E2): {ANA: C, BEN: C}, (H2, E2): {ANA: X, BEN: C},
    }  # fmt: skip
    result = ach.analyse([H1, H2], [H1, H2], EVIDENCE, ratings)
    assert result.raters == 2
    assert result.scores == {H1: -1.999, H2: -0.5}
    assert result.leading == H2
    assert result.disputed == [(H2, E2)]
    assert result.consensus[(H2, E2)] is None  # one each: no majority
    assert result.pivotal == {E1: H1}  # if E1 were wrong, H1 would lead


def test_a_tie_at_the_top_has_no_leader():
    ratings = {(H1, E2): {ANA: X}, (H2, E2): {ANA: X}}
    assert ach.analyse([H1, H2], [H1, H2], EVIDENCE, ratings).leading is None
