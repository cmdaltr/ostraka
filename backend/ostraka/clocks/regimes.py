"""Regulator clocks as fixed rules: each regime's milestones, what each one counts from, and the
deadline arithmetic. Pure functions, no database, so every rule has a unit test.

A milestone has one or more limits and is due at the earliest of them. A limit counts a span from
one of three moments:

- when you became aware of the incident (the incident's `aware_at`, or a clock's own start);
- when you classified it (DORA: as a major ICT-related incident);
- when the milestone before it was submitted. NIS2's final report and DORA's intermediate and final
  reports count from the report before them, not from awareness. Until that report is in, the
  deadline is counted from the earlier report's own deadline and marked provisional.

Hours are elapsed time: 72 h is 72 real hours, across a clock change. Days and months are calendar
spans read in the clock's time zone: one month from 31 January is the end of February, at the same
local time of day. A local time that doesn't exist on the day (the hour the clocks go forward) moves
on by the gap; one that happens twice (the hour they go back) is the first, the earlier deadline.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


class Unit(StrEnum):
    HOURS = "hours"
    DAYS = "days"
    MONTHS = "months"


class Basis(StrEnum):
    AWARE = "aware"
    CLASSIFIED = "classified"
    PREVIOUS = "previous"


class State(StrEnum):
    WAITING = "waiting"  # no deadline yet: it counts from a moment that hasn't happened
    RUNNING = "running"
    NEAR = "near"  # three quarters of the time gone
    CLOSING = "closing"  # nine tenths gone
    BREACHED = "breached"
    MET = "met"
    MET_LATE = "met_late"


# The share of a milestone's time at which its owner hears, then (from 90%) the commander too
NUDGE_AT = (0.5, 0.75, 0.9)
BREACHED = len(NUDGE_AT) + 1  # the nudge level for a deadline that has passed
COMMANDER_FROM = 3  # 90% and the breach go to the incident commander as well as the owner

# The most a custom span may be, per unit: two years either way
MAX_SPAN = {Unit.HOURS: 17_520, Unit.DAYS: 730, Unit.MONTHS: 24}
MAX_MILESTONES = 10


@dataclass(frozen=True)
class Span:
    amount: int
    unit: Unit

    def words(self) -> str:
        if self.unit is Unit.HOURS:
            return f"{self.amount} h"
        name = "day" if self.unit is Unit.DAYS else "month"
        return f"{self.amount} {name}{'' if self.amount == 1 else 's'}"


@dataclass(frozen=True)
class Limit:
    basis: Basis
    span: Span


@dataclass(frozen=True)
class Milestone:
    key: str
    name: str
    limits: tuple[Limit, ...]


@dataclass(frozen=True)
class Regime:
    key: str
    name: str
    authority: str  # who is told
    zone: str  # the time zone a calendar span is read in, unless the clock says otherwise
    milestones: tuple[Milestone, ...]
    source: str  # where the rule comes from
    custom: bool = False


def _limit(basis: Basis, amount: int, unit: Unit) -> Limit:
    return Limit(basis, Span(amount, unit))


H, M = Unit.HOURS, Unit.MONTHS
AWARE, CLASSIFIED, PREVIOUS = Basis.AWARE, Basis.CLASSIFIED, Basis.PREVIOUS

REGIMES: dict[str, Regime] = {
    r.key: r
    for r in (
        Regime(
            "uk_gdpr",
            "UK GDPR",
            "the ICO",
            "Europe/London",
            (Milestone("report", "Report to the ICO", (_limit(AWARE, 72, H),)),),
            "UK GDPR Article 33(1): without undue delay and, where feasible, within 72 hours of becoming "
            "aware of a personal data breach.",
        ),
        Regime(
            "uk_nis",
            "UK NIS",
            "the competent authority",
            "Europe/London",
            (Milestone("notify", "Notify the competent authority", (_limit(AWARE, 72, H),)),),
            "The NIS Regulations 2018, regulations 11 and 12: without undue delay and no later than "
            "72 hours after becoming aware of an incident.",
        ),
        Regime(
            "eu_nis2",
            "EU NIS2",
            "the CSIRT or competent authority",
            "Europe/Brussels",
            (
                Milestone("early_warning", "Early warning", (_limit(AWARE, 24, H),)),
                Milestone("notification", "Incident notification", (_limit(AWARE, 72, H),)),
                Milestone("final", "Final report", (_limit(PREVIOUS, 1, M),)),
            ),
            "NIS2 Directive (EU) 2022/2555, Article 23(4): an early warning within 24 hours of becoming "
            "aware, a notification within 72 hours, and a final report within one month of the "
            "notification.",
        ),
        Regime(
            "dora",
            "DORA",
            "the competent authority",
            "Europe/Brussels",
            (
                Milestone(
                    "initial",
                    "Initial notification",
                    (_limit(CLASSIFIED, 4, H), _limit(AWARE, 24, H)),
                ),
                Milestone("intermediate", "Intermediate report", (_limit(PREVIOUS, 72, H),)),
                Milestone("final", "Final report", (_limit(PREVIOUS, 1, M),)),
            ),
            "DORA (EU) 2022/2554, Article 19, with Delegated Regulation (EU) 2025/301, Article 5: the "
            "initial notification within 4 hours of classifying the incident as major and no later "
            "than 24 hours after becoming aware; the intermediate report within 72 hours of the "
            "initial notification; the final report within one month of the intermediate report.",
        ),
        Regime(
            "custom",
            "Custom",
            "",
            "Europe/London",
            (),
            "Your own: a contract, an insurer's policy or the board.",
            custom=True,
        ),
    )
}


# ---- the arithmetic ---------------------------------------------------------------------------------


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("A clock counts from an instant with a time zone, never a naive time.")
    return moment.astimezone(UTC)  # one zone throughout, so differences are always elapsed time


def add_months(naive: datetime, months: int) -> datetime:
    """A calendar month span on a wall-clock time: the same day of the month, or the month's last
    day when it has fewer (31 January plus one month is 28 or 29 February)."""
    index = naive.month - 1 + months
    year, month = naive.year + index // 12, index % 12 + 1
    day = min(naive.day, calendar.monthrange(year, month)[1])
    return naive.replace(year=year, month=month, day=day)


def add(start: datetime, span: Span, zone: str) -> datetime:
    """`start` plus `span`, as a UTC instant. Hours are elapsed; days and months are on the calendar
    in `zone`."""
    start = _aware(start)
    if span.unit is Unit.HOURS:
        # In UTC: Python adds a timedelta to a zoned time on the wall clock, which is wrong across a change
        return start.astimezone(UTC) + timedelta(hours=span.amount)
    tz = ZoneInfo(zone)
    local = start.astimezone(tz).replace(tzinfo=None)
    moved = local + timedelta(days=span.amount) if span.unit is Unit.DAYS else add_months(local, span.amount)
    # fold=0: a time that doesn't exist reads with the offset before the change, so it moves on by
    # the gap; a time that happens twice is its first occurrence
    return moved.replace(tzinfo=tz, fold=0).astimezone(UTC)


@dataclass(frozen=True)
class Due:
    """One milestone's deadline, and how it was reached."""

    key: str
    name: str
    due: datetime | None  # None while it waits for a moment that hasn't happened
    starts: datetime | None  # the moment its binding limit counts from
    provisional: bool  # counted from an earlier milestone's deadline because that one isn't in yet
    counted: str  # in words: "72 h from awareness"


def schedule(
    milestones: Sequence[Milestone],
    *,
    aware: datetime,
    classified: datetime | None = None,
    met: Mapping[str, datetime] | None = None,
    zone: str = "UTC",
) -> list[Due]:
    """Every milestone's deadline, in order. `met` gives when each milestone already submitted was
    submitted; a milestone that counts from the one before uses that time, or that one's deadline
    (provisionally) until it's in."""
    aware = _aware(aware)
    met = met or {}
    out: list[Due] = []
    for position, milestone in enumerate(milestones):
        before = out[position - 1] if position else None
        options: list[tuple[datetime, datetime, bool, str]] = []
        missing: list[str] = []
        waiting = ""
        for limit in milestone.limits:
            provisional = False
            if limit.basis is AWARE:
                base: datetime | None = aware
                words = "awareness"
            elif limit.basis is CLASSIFIED:
                base = _aware(classified) if classified else None
                words = "classification"
            else:
                if before is None:
                    raise ValueError(f"{milestone.name} counts from the milestone before it, and has none.")
                words = f"the {before.name.lower()}"
                base = met.get(before.key)
                base = _aware(base) if base else None
                if base is None:
                    base, provisional = before.due, True
            if base is None:
                waiting = waiting or words
                missing.append(f"{limit.span.words()} from {words}")
                continue
            options.append(
                (add(base, limit.span, zone), base, provisional, f"{limit.span.words()} from {words}")
            )
        if not options:
            out.append(Due(milestone.key, milestone.name, None, None, False, f"Waits for {waiting}"))
            continue
        due, base, provisional, counted = min(options, key=lambda o: o[0])
        if len(options) > 1:
            counted = "the earlier of " + " and ".join(o[3] for o in options)
        if missing:
            counted += f", or {' or '.join(missing)} once that's recorded, if sooner"
        if provisional:
            counted += ", provisional until it's submitted"
        out.append(Due(milestone.key, milestone.name, due, base, provisional, counted))
    return out


def fraction(starts: datetime, due: datetime, now: datetime) -> float:
    """How much of the milestone's time has gone: 0 at its start, 1 at the deadline."""
    window = (due - starts).total_seconds()
    if window <= 0:
        return 1.0 if now >= due else 0.0
    return (now - starts).total_seconds() / window


def level(starts: datetime, due: datetime, now: datetime) -> int:
    """The nudge level reached: 0 before half the time, 1 to 3 for each of `NUDGE_AT` passed, and
    `BREACHED` once the deadline has gone."""
    if now >= due:
        return BREACHED
    gone = fraction(starts, due, now)
    return sum(1 for share in NUDGE_AT if gone >= share)


def state(due: Due, met_at: datetime | None, now: datetime) -> State:
    if met_at is not None:
        return State.MET if due.due is None or met_at <= due.due else State.MET_LATE
    if due.due is None or due.starts is None:
        return State.WAITING
    reached = level(due.starts, due.due, now)
    if reached == BREACHED:
        return State.BREACHED
    if reached >= 3:
        return State.CLOSING
    if reached >= 2:
        return State.NEAR
    return State.RUNNING


def remaining_words(delta: timedelta) -> str:
    """'3 days 4 h', '18 h', '45 min': for nudges and the activity log."""
    seconds = abs(delta.total_seconds())
    days, rest = divmod(int(seconds), 86_400)
    hours, rest = divmod(rest, 3600)
    if days >= 2:
        return f"{days} days" + (f" {hours} h" if hours else "")
    if days or hours:
        return f"{days * 24 + hours} h"
    return f"{max(rest // 60, 1)} min"


# ---- custom milestones -------------------------------------------------------------------------------


def custom_milestones(definitions: Sequence[Mapping[str, object]]) -> tuple[Milestone, ...]:
    """A custom clock's milestones from their stored definitions, keyed m1, m2… by position.
    Each is {"name", "amount", "unit", "basis"}."""
    out = []
    for position, d in enumerate(definitions, start=1):
        limit = Limit(Basis(str(d["basis"])), Span(int(d["amount"]), Unit(str(d["unit"]))))  # type: ignore[call-overload]
        out.append(Milestone(f"m{position}", str(d["name"]), (limit,)))
    return tuple(out)


def check_custom(definitions: Sequence[Mapping[str, object]]) -> str | None:
    """Why a set of custom milestones can't be used, or None."""
    if not definitions:
        return "A custom clock needs at least one milestone."
    if len(definitions) > MAX_MILESTONES:
        return f"A clock can have at most {MAX_MILESTONES} milestones."
    for position, d in enumerate(definitions):
        try:
            unit, basis, amount = Unit(str(d["unit"])), Basis(str(d["basis"])), int(d["amount"])  # type: ignore[call-overload]
        except (KeyError, ValueError, TypeError):
            return f"Milestone {position + 1} needs a name, an amount, a unit and what it counts from."
        name = str(d.get("name", "")).strip()
        if not name or len(name) > 120:
            return f"Milestone {position + 1} needs a name of up to 120 characters."
        if not 1 <= amount <= MAX_SPAN[unit]:
            return f"Milestone {position + 1}: {unit.value} must be between 1 and {MAX_SPAN[unit]}."
        if basis is PREVIOUS and position == 0:
            return "The first milestone can't count from the one before it."
    return None


def milestones_of(regime: Regime, definitions: Sequence[Mapping[str, object]] = ()) -> tuple[Milestone, ...]:
    return custom_milestones(definitions) if regime.custom else regime.milestones


def needs_classification(milestones: Sequence[Milestone]) -> bool:
    return any(limit.basis is CLASSIFIED for m in milestones for limit in m.limits)
