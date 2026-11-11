"""Handover and presence without a database: the note builder, focus cleaning, the rule for mentioning
work in other incidents, and who has an object open."""

import time
import uuid
from datetime import UTC, datetime, timedelta

from ostraka import handover, live
from ostraka.auth import policy
from ostraka.incidents.presence import clean_focus
from ostraka.models import FindingStatus, TaskStatus, Tlp

NOW = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)


def state(**overrides) -> handover.State:
    base = dict(
        ref="OST-2026-014",
        title="Ransomware on the finance file servers",
        phase="contain",
        severity="critical",
        tlp="amber",
        closed=False,
        commander="Amara Okafor",
        author="Oliver Grant",
        now=NOW,
        since=NOW - timedelta(hours=10),
        from_position=12,
        through_position=15,
    )
    return handover.State(**{**base, **overrides})


def test_an_empty_shift_still_says_every_section_was_looked_at():
    made = handover.draft(state(through_position=12))
    titles = [s.title for s in made.sections]
    assert titles == [
        "State",
        "Deadlines in the next 24 hours",
        "Open tasks (0)",
        "Findings since 11 Nov 08:00 UTC",
        "Leads still open (0)",
        "Observables",
        "Discussion",
        "Activity (0 entries)",
    ]
    assert "No open tasks." in made.text and "Nothing recorded." in made.text
    assert "no new log entries" in made.text
    assert made.text.startswith("Handover: OST-2026-014 Ransomware on the finance file servers\n")


def test_tasks_are_ordered_by_deadline_with_owners_and_overdue_flagged():
    tasks = [
        handover.TaskLine(
            "TSK-9", "Restore payroll", "Jun Park", TaskStatus.BLOCKED, NOW + timedelta(hours=30), "vendor"
        ),
        handover.TaskLine(
            "TSK-4", "Image FS01", "Forensics queue, unclaimed", TaskStatus.OPEN, NOW - timedelta(hours=1)
        ),
        handover.TaskLine(
            "TSK-7", "Reset krbtgt", "Ellis Ward", TaskStatus.IN_PROGRESS, NOW + timedelta(hours=3)
        ),
        handover.TaskLine("TSK-2", "Brief the board", "Amara Okafor", TaskStatus.OPEN, None),
    ]
    made = handover.draft(state(open_tasks=tasks))
    deadlines, open_tasks = made.sections[1], made.sections[2]
    assert deadlines.lines == [
        "TSK-4 Image FS01: Forensics queue, unclaimed, overdue since 11 Nov 17:00 UTC",
        "TSK-7 Reset krbtgt: Ellis Ward, due 11 Nov 21:00 UTC",
    ]
    assert [line.split()[0] for line in open_tasks.lines] == ["TSK-4", "TSK-7", "TSK-9", "TSK-2"]
    assert open_tasks.lines[2] == "TSK-9 Restore payroll: Jun Park, blocked (vendor), due 13 Nov 00:00 UTC"


def test_findings_observables_and_the_log():
    made = handover.draft(
        state(
            new_findings=[handover.FindingLine("FND-3", "Rclone to Mega", FindingStatus.LEAD)],
            decided_findings=[
                handover.FindingLine("FND-1", "Initial access via VPN", FindingStatus.CONFIRMED)
            ],
            open_leads=[handover.FindingLine("FND-3", "Rclone to Mega", FindingStatus.LEAD)],
            new_observables=[
                handover.ObservableLine("OBS-4", "domain", "malicious"),
                handover.ObservableLine("OBS-5", "file_hash_sha256", "unknown"),
            ],
            open_threads=2,
            assessment="ASM-2, likely that data left the network",
            events=[handover.EventLine(NOW - timedelta(minutes=50 - i), f"event {i}") for i in range(45)],
        )
    )
    text = made.text
    assert "- New: FND-3 Rclone to Mega (lead)" in text
    assert "- Decided: FND-1 Initial access via VPN (confirmed)" in text
    assert "- 2 observables added, 1 malicious: OBS-4 (domain), OBS-5 (file hash sha256)" in text
    assert "- 2 unresolved threads." in text
    assert "Current assessment: ASM-2, likely that data left the network." in text
    log = made.sections[-1]
    assert log.title == "Activity (45 entries)"
    assert log.lines[0] == "(5 earlier entries not shown)" and len(log.lines) == 1 + handover.MAX_EVENTS
    assert log.lines[-1].endswith("event 44")


def test_the_same_snapshot_always_gives_the_same_note():
    snapshot = state(open_threads=1)
    assert handover.draft(snapshot) == handover.draft(snapshot)


def test_focus_is_a_section_or_an_object_id_and_nothing_else():
    oid = str(uuid.uuid4())
    assert clean_focus("findings") == "findings"
    assert clean_focus(f"finding:{oid}") == f"finding:{oid}"
    assert clean_focus(f"Observable:{oid.upper()}") == f"observable:{oid}"
    assert clean_focus(None) == ""
    for bad in (
        "observable:185.220.101.4",  # content, not an id
        f"secret:{oid}",
        "evil.example.com",
        "a" * 30,
        "findings; drop",
    ):
        assert clean_focus(bad) == "", bad


def test_work_elsewhere_is_mentioned_only_where_it_leaks_nothing_new():
    assert policy.may_signal_work_elsewhere(can_view=True, tlp=Tlp.RED)
    assert policy.may_signal_work_elsewhere(can_view=False, tlp=Tlp.AMBER)
    assert policy.may_signal_work_elsewhere(can_view=False, tlp=Tlp.CLEAR)
    assert not policy.may_signal_work_elsewhere(can_view=False, tlp=Tlp.RED)
    assert not policy.may_signal_work_elsewhere(can_view=False, tlp=Tlp.AMBER_STRICT)


def test_who_has_an_object_open_one_entry_per_person():
    hub = live.Hub()
    focus = f"observable:{uuid.uuid4()}"
    hub.presence["inc"]["c1"] = live.Viewer("u1", "Sam", focus, False)
    hub.presence["inc"]["c2"] = live.Viewer("u1", "Sam", focus, True)  # a second tab, editing
    hub.presence["inc"]["c3"] = live.Viewer("u2", "Dee", "findings", False)
    stale = live.Viewer("u3", "Gone", focus, False)
    stale.seen = time.monotonic() - live.PRESENCE_SECONDS - 1
    hub.presence["inc"]["c4"] = stale
    found = hub.focused("inc", focus)
    assert [(v.name, v.editing) for v in found] == [("Sam", True)]
    assert hub.focused("other", focus) == []
