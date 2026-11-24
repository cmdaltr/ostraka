"""The executive summary's own draft and the checks a model's wording must pass. Pure, no database."""

from datetime import UTC, datetime

from ostraka import executive
from ostraka.executive import Facts, FindingFact
from ostraka.models import FindingStatus

C, L, R = FindingStatus.CONFIRMED, FindingStatus.LEAD, FindingStatus.REFUTED
ASSESSED = "We assess it is likely (55–75%) that the actor staged the payroll export for theft."


def facts(**overrides) -> Facts:
    base = dict(
        ref="OST-26-014",
        codename="Folded Lantern",
        title="Mailbox rule forwards finance mail out",
        kind="incident",
        severity="high",
        tlp="amber",
        phase="contain",
        closed=False,
        declared_at=datetime(2026, 11, 3, 9, tzinfo=UTC),
        closed_at=None,
        findings=[
            FindingFact("FND-3", "Inbox rule created by the attacker", C, techniques=("T1114.003",)),
            FindingFact("FND-4", "Payroll export staged", C),
            FindingFact("FND-5", "Rclone seen on FIN-WS-07", L),
            FindingFact("FND-6", "Beacon to 185.220.101.4", R),
        ],
        assets=2,
        assessment_ref="ASM-2",
        assessment=ASSESSED,
        comments=["Dana Lead: I think FND-5 is the exfil, but it's not proven yet"],
    )
    return Facts(**{**base, **overrides})


def test_ostrakas_draft_is_four_sentences_what_it_is_findings_and_conclusion():
    s = executive.draft(facts())
    assert s == [
        "OST-26-014 Folded Lantern is a high-severity incident at TLP:AMBER, “Mailbox rule forwards finance "
        "mail out”, declared on 3 November 2026, still open in the contain phase.",
        "2 findings have been confirmed: FND-3 (Inbox rule created by the attacker) and FND-4 (Payroll "
        "export staged).",
        "The confirmed findings map to ATT&CK T1114.003; 2 assets are recorded; 1 lead remains to be "
        "confirmed or refuted; 1 finding was refuted.",
        "We assess it is likely (55–75%) that the actor staged the payroll export for theft (ASM-2).",
    ]
    assert executive.problems(facts(), s) == []


def test_with_nothing_confirmed_or_assessed_it_says_so():
    f = facts(
        findings=[FindingFact("FND-5", "Rclone seen", L)],
        assessment=None,
        assessment_ref=None,
        assets=0,
        closed=True,
        closed_at=datetime(2026, 11, 20, tzinfo=UTC),
        kind="hunt",
    )
    s = executive.draft(f)
    assert "hunt at TLP:AMBER" in s[0] and "closed on 20 November 2026" in s[0]
    assert s[1] == "No finding has been confirmed; 1 lead is still being worked."
    assert s[3].startswith("The hunt was closed without a current assessment")
    assert executive.problems(f, s) == []


def test_the_draft_masks_indicators():
    f = facts(title="Beacons to 185.220.101.4")
    assert "185.220.101.4" not in " ".join(executive.draft(f))


def test_the_model_reads_comments_and_the_summary_masked():
    f = facts(summary="Seen talking to 185.220.101.4", comments=["Sam: blocked 185.220.101.4 at the edge"])
    text = executive.prompt(f)
    assert "185.220.101.4" not in text and "[ip address]" in text
    assert "Sam: blocked" in text and "Seen talking to" in text and "FND-5 (lead)" in text


def good() -> list[str]:
    return [
        "OST-26-014 Folded Lantern is a high-severity incident, still open.",
        "The attacker set an inbox rule (FND-3) and staged the payroll export (FND-4), both confirmed.",
        "Two assets are involved and one lead is still open.",
        "We assess it is likely (55–75%) that the actor staged the payroll export for theft (ASM-2).",
    ]


def test_a_sound_wording_passes():
    assert executive.problems(facts(), good()) == []


def test_a_lead_or_refuted_finding_called_confirmed_is_set_aside():
    s = good()
    s[2] = "FND-5 confirmed the rclone exfiltration and FND-6 confirmed the beacon."
    out = executive.problems(facts(), s)
    assert "It calls FND-5 confirmed, but it is still a lead." in out
    assert "It calls FND-6 confirmed, but it was refuted." in out


def test_shape_references_and_wording_are_checked():
    f = facts()
    assert executive.problems(f, good()[:3]) == ["It wrote 3 sentences, not 4."]
    s = good()
    s[0] = "Folded Lantern is an incident."
    s[1] = "FND-3 and FND-99 were confirmed."
    s[3] = "It is highly likely (80–90%) that this clearly was a massive theft."
    out = executive.problems(f, s)
    assert "The first sentence doesn't name OST-26-014." in out
    assert "It cites FND-99, which the record doesn't hold." in out
    assert "The findings sentences don't cite the confirmed findings." in out
    assert "The conclusion doesn't cite ASM-2." in out
    assert "It uses a probability term the current assessment doesn't." in out
    assert "It asserts certainty in words, which only the evidence may." in out
    assert "It uses loaded wording." in out


def test_likelihood_words_outside_the_yardstick_and_claims_with_nothing_confirmed():
    f = facts(findings=[FindingFact("FND-5", "Rclone seen", L)], assessment=None, assessment_ref=None)
    s = [
        "OST-26-014 Folded Lantern is an open incident.",
        "The actor probably exfiltrated data, which is confirmed.",
        "No finding has been confirmed yet; FND-5 is a lead.",
        "No judgement has been made yet.",
    ]
    out = executive.problems(f, s)
    assert "It uses likelihood words outside the yardstick." in out
    assert "It says something is confirmed, but no finding is." in out


def test_parse_reads_the_json_array():
    assert executive.parse('Here:\n["One.", "Two  words."]') == ["One.", "Two words."]
    assert executive.parse("no array") is None
    assert executive.parse("[1, 2]") is None
