"""The regulator clock rules and their arithmetic, without a database."""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ostraka.auth import policy
from ostraka.clocks import regimes
from ostraka.clocks.regimes import Basis, Span, State, Unit, add, add_months, schedule
from ostraka.models import IncidentRole, Role, User

LONDON = ZoneInfo("Europe/London")
BRUSSELS = ZoneInfo("Europe/Brussels")


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def by_key(dues):
    return {d.key: d for d in dues}


# ---- spans --------------------------------------------------------------------------------------------


def test_hours_are_elapsed_time_across_a_clock_change():
    # 09:00 BST on Saturday 24 October 2026; the clocks go back early on Sunday 25th
    aware = datetime(2026, 10, 24, 9, 0, tzinfo=LONDON)
    due = add(aware, Span(72, Unit.HOURS), "Europe/London")
    assert due == utc(2026, 10, 27, 8, 0)  # 72 real hours: 08:00 GMT on the local clock, not 09:00
    assert due.astimezone(LONDON).hour == 8


def test_hours_ignore_the_zone_they_are_read_in():
    aware = utc(2026, 3, 28, 12, 0)
    assert add(aware, Span(24, Unit.HOURS), "Europe/London") == add(aware, Span(24, Unit.HOURS), "Asia/Tokyo")


def test_a_month_keeps_the_local_time_of_day_across_a_clock_change():
    aware = datetime(2026, 10, 10, 9, 0, tzinfo=LONDON)  # 08:00 UTC, in summer time
    due = add(aware, Span(1, Unit.MONTHS), "Europe/London")
    assert due == utc(2026, 11, 10, 9, 0)  # 09:00 GMT
    assert due.astimezone(LONDON).replace(tzinfo=None) == datetime(2026, 11, 10, 9, 0)


def test_a_month_from_the_end_of_a_long_month_is_the_end_of_a_short_one():
    assert add_months(datetime(2027, 1, 31, 10), 1) == datetime(2027, 2, 28, 10)
    assert add_months(datetime(2028, 1, 31, 10), 1) == datetime(2028, 2, 29, 10)  # a leap year
    assert add_months(datetime(2026, 8, 31, 10), 1) == datetime(2026, 9, 30, 10)
    assert add_months(datetime(2026, 12, 15, 10), 1) == datetime(2027, 1, 15, 10)  # over the year end
    assert add_months(datetime(2026, 11, 30, 10), 3) == datetime(2027, 2, 28, 10)
    assert add_months(datetime(2026, 2, 28, 10), 12) == datetime(2027, 2, 28, 10)


def test_a_month_counts_on_the_calendar_of_the_clocks_zone():
    # 23:30 UTC on 31 January is already 1 February in Brussels, so a month on is 1 March there
    aware = utc(2027, 1, 31, 23, 30)
    assert add(aware, Span(1, Unit.MONTHS), "Europe/Brussels") == datetime(2027, 3, 1, 0, 30, tzinfo=BRUSSELS)
    # and in London it's still 31 January: the end of February
    assert add(aware, Span(1, Unit.MONTHS), "Europe/London") == utc(2027, 2, 28, 23, 30)


def test_a_local_time_that_doesnt_exist_moves_on_by_the_gap():
    # 01:30 on 28 February 2027; 28 March 2027 is when London's clocks jump from 01:00 to 02:00
    aware = datetime(2027, 2, 28, 1, 30, tzinfo=LONDON)
    due = add(aware, Span(1, Unit.MONTHS), "Europe/London")
    assert due == utc(2027, 3, 28, 1, 30)
    assert due.astimezone(LONDON).replace(tzinfo=None) == datetime(2027, 3, 28, 2, 30)


def test_a_local_time_that_happens_twice_is_the_earlier_one():
    # 01:30 on 25 September 2026; on 25 October 01:30 comes twice in London, first in BST
    aware = datetime(2026, 9, 25, 1, 30, tzinfo=LONDON)
    assert add(aware, Span(1, Unit.MONTHS), "Europe/London") == utc(2026, 10, 25, 0, 30)


def test_days_are_calendar_days_in_the_zone():
    aware = datetime(2026, 10, 24, 9, 0, tzinfo=LONDON)
    assert add(aware, Span(2, Unit.DAYS), "Europe/London") == utc(2026, 10, 26, 9, 0)  # 49 real hours


def test_any_instant_with_an_offset_works_and_a_naive_one_is_refused():
    plus_two = timezone(timedelta(hours=2))
    assert add(datetime(2026, 11, 1, 12, tzinfo=plus_two), Span(72, Unit.HOURS), "UTC") == utc(
        2026, 11, 4, 10
    )
    with pytest.raises(ValueError):
        add(datetime(2026, 11, 1, 12), Span(72, Unit.HOURS), "UTC")
    with pytest.raises(ValueError):
        schedule(regimes.REGIMES["uk_gdpr"].milestones, aware=datetime(2026, 11, 1, 12))


def test_span_words():
    assert Span(72, Unit.HOURS).words() == "72 h"
    assert Span(1, Unit.MONTHS).words() == "1 month"
    assert Span(30, Unit.DAYS).words() == "30 days"


# ---- the regimes ----------------------------------------------------------------------------------------


AWARE = utc(2026, 11, 2, 9, 0)


def test_uk_gdpr_and_uk_nis_are_72_hours_from_awareness():
    for key in ("uk_gdpr", "uk_nis"):
        (due,) = schedule(regimes.REGIMES[key].milestones, aware=AWARE, zone="Europe/London")
        assert due.due == utc(2026, 11, 5, 9, 0) and due.starts == AWARE
        assert due.counted == "72 h from awareness" and not due.provisional


def test_nis2_final_report_counts_a_month_from_the_notification():
    rules = regimes.REGIMES["eu_nis2"]
    dues = by_key(schedule(rules.milestones, aware=AWARE, zone=rules.zone))
    assert dues["early_warning"].due == utc(2026, 11, 3, 9, 0)
    assert dues["notification"].due == utc(2026, 11, 5, 9, 0)
    # Not yet submitted: provisional, a month from the notification's own deadline (in Brussels)
    assert dues["final"].due == utc(2026, 12, 5, 9, 0) and dues["final"].provisional
    assert dues["final"].counted.endswith("provisional until it's submitted")

    submitted = utc(2026, 11, 4, 16, 45)
    dues = by_key(schedule(rules.milestones, aware=AWARE, met={"notification": submitted}, zone=rules.zone))
    assert dues["final"].due == utc(2026, 12, 4, 16, 45) and not dues["final"].provisional
    assert dues["final"].counted == "1 month from the incident notification"


def test_dora_initial_is_the_earlier_of_4_hours_from_classification_and_24_from_awareness():
    rules = regimes.REGIMES["dora"]
    # Not classified yet: 24 h from awareness, and it says it may come sooner
    initial = schedule(rules.milestones, aware=AWARE, zone=rules.zone)[0]
    assert initial.due == utc(2026, 11, 3, 9, 0) and initial.starts == AWARE
    assert "4 h from classification once that's recorded" in initial.counted

    # Classified early: 4 h from classification wins
    initial = schedule(rules.milestones, aware=AWARE, classified=utc(2026, 11, 2, 11, 0), zone=rules.zone)[0]
    assert initial.due == utc(2026, 11, 2, 15, 0) and initial.starts == utc(2026, 11, 2, 11, 0)
    assert initial.counted == "the earlier of 4 h from classification and 24 h from awareness"

    # Classified late: 24 h from awareness still holds
    initial = schedule(rules.milestones, aware=AWARE, classified=utc(2026, 11, 3, 7, 0), zone=rules.zone)[0]
    assert initial.due == utc(2026, 11, 3, 9, 0) and initial.starts == AWARE

    # Exactly together: either way it's the same moment
    initial = schedule(rules.milestones, aware=AWARE, classified=utc(2026, 11, 3, 5, 0), zone=rules.zone)[0]
    assert initial.due == utc(2026, 11, 3, 9, 0)


def test_dora_reports_count_from_the_one_before():
    rules = regimes.REGIMES["dora"]
    classified = utc(2026, 11, 2, 11, 0)
    dues = by_key(schedule(rules.milestones, aware=AWARE, classified=classified, zone=rules.zone))
    assert dues["intermediate"].due == utc(2026, 11, 5, 15, 0) and dues["intermediate"].provisional
    assert dues["final"].due == utc(2026, 12, 5, 15, 0) and dues["final"].provisional

    met = {"initial": utc(2026, 11, 2, 13, 20), "intermediate": utc(2026, 11, 4, 10, 0)}
    dues = by_key(schedule(rules.milestones, aware=AWARE, classified=classified, met=met, zone=rules.zone))
    assert dues["intermediate"].due == utc(2026, 11, 5, 13, 20) and not dues["intermediate"].provisional
    assert dues["final"].due == utc(2026, 12, 4, 10, 0) and not dues["final"].provisional


def test_every_built_in_regime_matches_the_roadmap():
    assert set(regimes.REGIMES) == {"uk_gdpr", "uk_nis", "eu_nis2", "dora", "custom"}
    assert regimes.needs_classification(regimes.REGIMES["dora"].milestones)
    assert not regimes.needs_classification(regimes.REGIMES["eu_nis2"].milestones)
    assert regimes.REGIMES["custom"].custom and not regimes.REGIMES["custom"].milestones


# ---- custom clocks --------------------------------------------------------------------------------------


INSURER = [
    {"name": "Tell the insurer", "amount": 48, "unit": "hours", "basis": "aware"},
    {"name": "Proof of loss", "amount": 30, "unit": "days", "basis": "previous"},
]


def test_custom_milestones_are_keyed_by_position_and_count_as_defined():
    milestones = regimes.custom_milestones(INSURER)
    assert [m.key for m in milestones] == ["m1", "m2"]
    dues = schedule(milestones, aware=AWARE, met={"m1": utc(2026, 11, 3, 10, 0)}, zone="Europe/London")
    assert dues[0].due == utc(2026, 11, 4, 9, 0)
    assert dues[1].due == utc(2026, 12, 3, 10, 0)


def test_a_custom_milestone_counting_from_classification_waits_for_it():
    board = regimes.custom_milestones(
        [{"name": "Brief the board", "amount": 2, "unit": "hours", "basis": "classified"}]
    )
    (due,) = schedule(board, aware=AWARE)
    assert due.due is None and due.counted == "Waits for classification"
    assert regimes.state(due, None, AWARE) is State.WAITING


@pytest.mark.parametrize(
    ("definitions", "words"),
    [
        ([], "at least one"),
        ([{"name": "x", "amount": 0, "unit": "hours", "basis": "aware"}], "between 1 and"),
        ([{"name": "x", "amount": 25, "unit": "months", "basis": "aware"}], "between 1 and 24"),
        ([{"name": "", "amount": 1, "unit": "hours", "basis": "aware"}], "name"),
        ([{"name": "x", "amount": 1, "unit": "weeks", "basis": "aware"}], "needs a name, an amount"),
        ([{"name": "x", "amount": 1, "unit": "hours", "basis": "previous"}], "first milestone"),
        ([{"name": "x", "amount": 1, "unit": "hours", "basis": "aware"}] * 11, "at most 10"),
    ],
)
def test_bad_custom_milestones_are_refused(definitions, words):
    assert words in (regimes.check_custom(definitions) or "")


def test_good_custom_milestones_pass():
    assert regimes.check_custom(INSURER) is None


# ---- progress, nudge levels and states ------------------------------------------------------------------


def test_nudge_levels_at_half_three_quarters_nine_tenths_and_the_breach():
    start, due = AWARE, AWARE + timedelta(hours=72)
    at = lambda hours: start + timedelta(hours=hours)  # noqa: E731
    assert regimes.level(start, due, at(35.9)) == 0
    assert regimes.level(start, due, at(36)) == 1
    assert regimes.level(start, due, at(54)) == 2
    assert regimes.level(start, due, at(64.8)) == 3
    assert regimes.level(start, due, at(71.99)) == 3
    assert regimes.level(start, due, at(72)) == regimes.BREACHED
    assert regimes.level(start, due, at(-5)) == 0  # before it starts


def test_a_deadline_at_or_before_its_start_is_due_at_once():
    assert regimes.level(AWARE, AWARE, AWARE) == regimes.BREACHED
    assert regimes.fraction(AWARE, AWARE, AWARE - timedelta(seconds=1)) == 0.0


def test_states_follow_the_time_gone_and_whether_it_was_met_in_time():
    (due,) = schedule(regimes.REGIMES["uk_gdpr"].milestones, aware=AWARE)
    at = lambda hours: AWARE + timedelta(hours=hours)  # noqa: E731
    assert regimes.state(due, None, at(10)) is State.RUNNING
    assert regimes.state(due, None, at(40)) is State.RUNNING
    assert regimes.state(due, None, at(55)) is State.NEAR
    assert regimes.state(due, None, at(66)) is State.CLOSING
    assert regimes.state(due, None, at(73)) is State.BREACHED
    assert regimes.state(due, at(71), at(80)) is State.MET
    assert regimes.state(due, at(72), at(80)) is State.MET  # on the deadline is in time
    assert regimes.state(due, at(75), at(80)) is State.MET_LATE


def test_remaining_words():
    assert regimes.remaining_words(timedelta(minutes=45)) == "45 min"
    assert regimes.remaining_words(timedelta(seconds=20)) == "1 min"
    assert regimes.remaining_words(timedelta(hours=18, minutes=10)) == "18 h"
    assert regimes.remaining_words(timedelta(hours=30)) == "30 h"
    assert regimes.remaining_words(timedelta(days=3, hours=4)) == "3 days 4 h"
    assert regimes.remaining_words(timedelta(days=-2)) == "2 days"


def test_basis_values_are_what_the_api_takes():
    assert {b.value for b in Basis} == {"aware", "classified", "previous"}


# ---- who may do what ------------------------------------------------------------------------------------


def person(role: Role) -> User:
    return User(name="x", email="x@example.org", role=role)


@pytest.mark.parametrize(
    ("role", "incident_role", "allowed"),
    [
        (Role.RESPONDER, IncidentRole.COMMANDER, True),
        (Role.RESPONDER, IncidentRole.COMMS, True),
        (Role.RESPONDER, IncidentRole.SCRIBE, False),
        (Role.RESPONDER, IncidentRole.RESPONDER, False),
        (Role.RESPONDER, None, False),
        (Role.TEAM_LEAD, None, False),
        (Role.TEAM_LEAD, IncidentRole.COMMS, True),
        (Role.ADMIN, None, True),
        (Role.OBSERVER, IncidentRole.COMMS, False),
    ],
)
def test_who_may_manage_clocks(role, incident_role, allowed):
    assert (policy.can_manage_clocks(person(role), incident_role) is None) is allowed


def test_the_owner_may_mark_milestones_but_not_manage_the_clock():
    owner = person(Role.RESPONDER)
    assert policy.can_mark_milestone(owner, None, is_owner=True) is None
    assert policy.can_manage_clocks(owner, None) is not None
    assert "owner" in (policy.can_mark_milestone(owner, None, is_owner=False) or "")
    assert policy.can_mark_milestone(person(Role.RESPONDER), IncidentRole.COMMS, is_owner=False) is None
    assert "Observers" in (policy.can_mark_milestone(person(Role.OBSERVER), None, is_owner=True) or "")
