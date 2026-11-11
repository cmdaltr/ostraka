"""The shift-handover note and "since you left", built from the activity log and the incident as it is.

Nothing here is AI. `draft()` is a pure function of a snapshot (`State`), so the same log and state
always give the same note; `gather()` takes the snapshot from the database. The person handing over
edits the text and posts it (`api/handover.py`); what Ostraka generated is kept beside what was posted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.models import (
    FINISHED,
    ActivityEvent,
    Assessment,
    AssessmentStatus,
    Comment,
    Finding,
    FindingStatus,
    HandoverNote,
    Incident,
    IncidentObservable,
    IncidentRole,
    Task,
    TaskStatus,
    User,
    utcnow,
)

DUE_SOON = timedelta(hours=24)
MAX_EVENTS = 40  # the log lines quoted in a note; the rest are counted


@dataclass(frozen=True)
class EventLine:
    at: datetime
    summary: str


@dataclass(frozen=True)
class TaskLine:
    ref: str
    title: str
    owner: str
    status: TaskStatus
    due_at: datetime | None
    blocked_reason: str = ""


@dataclass(frozen=True)
class FindingLine:
    ref: str
    title: str
    status: FindingStatus


@dataclass(frozen=True)
class ObservableLine:
    ref: str
    kind: str
    verdict: str


@dataclass
class State:
    """Everything a handover note is written from."""

    ref: str
    title: str
    phase: str
    severity: str
    tlp: str
    closed: bool
    commander: str | None
    author: str
    now: datetime
    since: datetime
    from_position: int
    through_position: int
    events: list[EventLine] = field(default_factory=list)
    open_tasks: list[TaskLine] = field(default_factory=list)
    new_findings: list[FindingLine] = field(default_factory=list)
    decided_findings: list[FindingLine] = field(default_factory=list)
    open_leads: list[FindingLine] = field(default_factory=list)
    new_observables: list[ObservableLine] = field(default_factory=list)
    open_threads: int = 0
    assessment: str | None = None  # "ASM-4: highly likely that …"


@dataclass(frozen=True)
class Section:
    title: str
    lines: list[str]


@dataclass(frozen=True)
class Draft:
    sections: list[Section]
    text: str


def when(at: datetime) -> str:
    return f"{at:%d %b %H:%M} UTC"


def _task_line(task: TaskLine, now: datetime) -> str:
    parts = [f"{task.ref} {task.title}: {task.owner}"]
    if task.status is TaskStatus.BLOCKED:
        parts.append("blocked" + (f" ({task.blocked_reason})" if task.blocked_reason else ""))
    if task.due_at is not None:
        parts.append(
            f"overdue since {when(task.due_at)}" if task.due_at <= now else f"due {when(task.due_at)}"
        )
    return ", ".join(parts)


def _counted(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def draft(state: State) -> Draft:
    """The note, section by section. Empty sections say so, so the reader knows nothing was left out."""
    now = state.now
    sections: list[Section] = []

    head = [
        f"Phase {state.phase}, severity {state.severity}, TLP:{state.tlp.upper()}"
        + (", closed." if state.closed else ", open."),
        f"Commander: {state.commander or 'nobody holds command'}.",
    ]
    if state.assessment:
        head.append(f"Current assessment: {state.assessment}.")
    sections.append(Section("State", head))

    due = sorted(
        (t for t in state.open_tasks if t.due_at is not None and t.due_at <= now + DUE_SOON),
        key=lambda t: t.due_at,  # type: ignore[arg-type,return-value]
    )
    sections.append(
        Section(
            "Deadlines in the next 24 hours",
            [_task_line(t, now) for t in due] or ["None overdue or due."],
        )
    )
    ordered = sorted(state.open_tasks, key=lambda t: (t.due_at is None, t.due_at or now, t.ref))
    sections.append(
        Section(
            f"Open tasks ({len(ordered)})",
            [_task_line(t, now) for t in ordered] or ["No open tasks."],
        )
    )

    findings = [f"New: {f.ref} {f.title} ({f.status.value})" for f in state.new_findings]
    findings += [f"Decided: {f.ref} {f.title} ({f.status.value})" for f in state.decided_findings]
    sections.append(
        Section("Findings since " + when(state.since), findings or ["No new or decided findings."])
    )
    sections.append(
        Section(
            f"Leads still open ({len(state.open_leads)})",
            [f"{f.ref} {f.title}" for f in state.open_leads] or ["No open leads."],
        )
    )

    if state.new_observables:
        malicious = sum(1 for o in state.new_observables if o.verdict == "malicious")
        observables = [
            f"{_counted(len(state.new_observables), 'observable')} added"
            + (f", {malicious} malicious" if malicious else "")
            + ": "
            + ", ".join(f"{o.ref} ({o.kind.replace('_', ' ')})" for o in state.new_observables)
        ]
    else:
        observables = ["No observables added."]
    sections.append(Section("Observables", observables))
    sections.append(
        Section(
            "Discussion",
            [f"{_counted(state.open_threads, 'unresolved thread')}."]
            if state.open_threads
            else ["No unresolved threads."],
        )
    )

    events = state.events[-MAX_EVENTS:]
    log = [f"{e.at:%d %b %H:%M} {e.summary}" for e in events]
    if len(state.events) > len(events):
        log.insert(
            0, f"({_counted(len(state.events) - len(events), 'earlier entry', 'earlier entries')} not shown)"
        )
    sections.append(
        Section(f"Activity ({_counted(len(state.events), 'entry', 'entries')})", log or ["Nothing recorded."])
    )

    covering = (
        f"log entries {state.from_position + 1}–{state.through_position}"
        if state.through_position > state.from_position
        else "no new log entries"
    )
    lines = [
        f"Handover: {state.ref} {state.title}",
        f"From {state.author} at {when(now)}, covering {when(state.since)} onwards ({covering}).",
    ]
    for section in sections:
        lines += ["", section.title, *(f"- {line}" for line in section.lines)]
    return Draft(sections, "\n".join(lines))


def _owner(task: Task) -> str:
    if task.assignee is not None:
        return task.assignee.name
    if task.team is not None:
        return f"{task.team.name} queue, unclaimed"
    return "nobody"


async def last_handover(db: AsyncSession, incident_id: uuid.UUID) -> HandoverNote | None:
    return await db.scalar(
        select(HandoverNote)
        .where(HandoverNote.incident_id == incident_id)
        .order_by(HandoverNote.through_position.desc(), HandoverNote.created_at.desc())
        .limit(1)
    )


async def through(db: AsyncSession, incident_id: uuid.UUID) -> int:
    """The last position in the incident's activity log."""
    return (
        await db.scalar(
            select(func.max(ActivityEvent.position)).where(ActivityEvent.incident_id == incident_id)
        )
        or 0
    )


async def gather(db: AsyncSession, incident: Incident, author: User, after: int | None = None) -> State:
    """The snapshot for a note covering the log after position `after`: by default, everything since
    the last handover posted on the incident (or since it was opened)."""
    if after is None:
        previous = await last_handover(db, incident.id)
        after = previous.through_position if previous else 0
    last = await through(db, incident.id)
    after = max(0, min(after, last))
    events = (
        await db.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.incident_id == incident.id, ActivityEvent.position > after)
            .order_by(ActivityEvent.position)
        )
    ).all()
    since = (
        await db.scalar(
            select(ActivityEvent.at).where(
                ActivityEvent.incident_id == incident.id, ActivityEvent.position == after
            )
        )
        if after
        else None
    ) or incident.declared_at

    tasks = (
        await db.scalars(
            select(Task)
            .where(Task.incident_id == incident.id, Task.status.not_in(FINISHED))
            .order_by(Task.seq)
        )
    ).all()
    findings = (
        await db.scalars(select(Finding).where(Finding.incident_id == incident.id).order_by(Finding.seq))
    ).all()
    line = lambda f: FindingLine(f"FND-{f.seq}", f.title, f.status)  # noqa: E731
    observables = (
        await db.scalars(
            select(IncidentObservable)
            .where(IncidentObservable.incident_id == incident.id, IncidentObservable.added_at > since)
            .order_by(IncidentObservable.added_at)
        )
    ).all()
    threads = (
        await db.scalar(
            select(func.count()).where(
                Comment.incident_id == incident.id,
                Comment.parent_id.is_(None),
                Comment.deleted_at.is_(None),
                Comment.resolved_at.is_(None),
            )
        )
        or 0
    )
    current = await db.scalar(
        select(Assessment)
        .where(Assessment.incident_id == incident.id, Assessment.status == AssessmentStatus.CURRENT)
        .order_by(Assessment.created_at.desc())
        .limit(1)
    )
    commander = next((m.user.name for m in incident.members if m.role is IncidentRole.COMMANDER), None)
    return State(
        ref=incident.ref,
        title=incident.title,
        phase=incident.phase.value,
        severity=incident.severity.value,
        tlp=incident.tlp.value,
        closed=incident.closed_at is not None,
        commander=commander,
        author=author.name,
        now=utcnow(),
        since=since,
        from_position=after,
        through_position=last,
        events=[EventLine(e.at, e.summary) for e in events],
        open_tasks=[TaskLine(t.ref, t.title, _owner(t), t.status, t.due_at, t.blocked_reason) for t in tasks],
        new_findings=[line(f) for f in findings if f.created_at > since],
        decided_findings=[
            line(f)
            for f in findings
            if f.decided_at is not None and f.decided_at > since and f.created_at <= since
        ],
        open_leads=[line(f) for f in findings if f.status is FindingStatus.LEAD],
        new_observables=[
            ObservableLine(f"OBS-{o.observable.seq}", o.observable.kind.value, o.verdict.value)
            for o in observables
        ],
        open_threads=threads,
        assessment=(
            f"ASM-{current.seq}, {current.probability.value.replace('_', ' ')} that {current.judgement}"
            if current
            else None
        ),
    )
