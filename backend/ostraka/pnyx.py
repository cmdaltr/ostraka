"""Pnyx: where an incident stands and what it needs next, for whoever has just walked in.

The bearings are Ostraka's own: `bearings()` answers the standing questions (what do I do now, what's
outstanding, the last update, the biggest concern, what to tell leadership) as a pure function of a
snapshot (`Facts`), by fixed rules, with every line citing the records it rests on. They work with AI
switched off.

A free question is the one AI path here. The model gets the bearings and the record, indicators masked,
and must answer from them alone, citing references. `problems()` checks what comes back without a model:
an answer that cites a reference the record doesn't hold, calls a lead confirmed, or asserts certainty in
words is set aside. The TLP guard is the stakeholder updates' (`updates.ai.provider_for`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.clocks import regimes, service
from ostraka.handover import when
from ostraka.incidents.masking import mask_text
from ostraka.models import (
    FINISHED,
    ActivityEvent,
    Clock,
    Comment,
    Finding,
    FindingStatus,
    Incident,
    IncidentRole,
    Task,
    TaskStatus,
    User,
)
from ostraka.standard import claims, yardstick
from ostraka.standard.rephrase import REFS
from ostraka.updates import drafts, rhythm

SOON = timedelta(hours=24)
LISTED = 8  # lines of one kind in an answer; the rest are counted
CONTEXT_EVENTS = 40  # recent log entries a free question is answered from

QUESTIONS: dict[str, str] = {
    "now": "What do I do now?",
    "outstanding": "What’s outstanding?",
    "last_update": "What was the last update?",
    "concern": "What’s the biggest concern?",
    "leadership": "What do I tell leadership?",
}


@dataclass(frozen=True)
class TaskFact:
    ref: str
    title: str
    owner: str
    mine: bool  # assigned to the person asking
    my_queue: bool  # unclaimed in the asker's team's queue
    status: TaskStatus
    due_at: datetime | None
    blocked_reason: str = ""


@dataclass(frozen=True)
class FindingFact:
    ref: str
    title: str
    status: FindingStatus
    statement: str = ""


@dataclass(frozen=True)
class DeadlineFact:
    ref: str  # CLK-3
    clock: str  # "GDPR"
    milestone: str  # "Notify the ICO"
    due: datetime


@dataclass(frozen=True)
class UpdateFact:
    ref: str  # UPD-7
    number: int
    sent_at: datetime
    lines: tuple[str, ...]


@dataclass
class Facts:
    """Everything the bearings are worked out from, already masked where the asker can't see indicators."""

    ref: str
    title: str
    codename: str
    kind: str  # incident or hunt
    phase: str
    severity: str
    tlp: str
    closed: bool
    now: datetime
    asker: str
    asker_role: IncidentRole | None
    commander: str | None
    tasks: list[TaskFact] = field(default_factory=list)
    findings: list[FindingFact] = field(default_factory=list)
    deadlines: list[DeadlineFact] = field(default_factory=list)
    last_update: UpdateFact | None = None
    update_rhythm: str = "on change only"
    next_update_due: datetime | None = None
    assessment: str | None = None  # "ASM-4: it is highly likely (80–90%) that …"
    open_threads: int = 0
    events: list[str] = field(default_factory=list)  # "14 Nov 11:20 Dana Lead added FND-3"

    @property
    def leads(self) -> list[FindingFact]:
        return [f for f in self.findings if f.status is FindingStatus.LEAD]

    @property
    def confirmed(self) -> list[FindingFact]:
        return [f for f in self.findings if f.status is FindingStatus.CONFIRMED]


@dataclass(frozen=True)
class Answer:
    key: str
    question: str
    lines: list[str]


# ---- the bearings -------------------------------------------------------------------------------------


def _due(at: datetime, now: datetime) -> str:
    return f"overdue since {when(at)}" if at <= now else f"due {when(at)}"


def _task(t: TaskFact, now: datetime, owner: bool = True) -> str:
    parts = [f"{t.ref} {t.title}"]
    if owner:
        parts.append(t.owner)
    if t.status is TaskStatus.BLOCKED:
        parts.append("blocked" + (f" ({t.blocked_reason})" if t.blocked_reason else ""))
    if t.due_at is not None:
        parts.append(_due(t.due_at, now))
    return ", ".join(parts)


def _by_due(tasks: list[TaskFact], now: datetime) -> list[TaskFact]:
    return sorted(tasks, key=lambda t: (t.due_at is None, t.due_at or now, t.ref))


def _capped(lines: list[str], more: str) -> list[str]:
    if len(lines) <= LISTED:
        return lines
    return [*lines[:LISTED], f"…and {len(lines) - LISTED} more {more}."]


def _deadline(d: DeadlineFact, now: datetime) -> str:
    return f"{d.ref} {d.clock}: {d.milestone}, {_due(d.due, now)}"


def _update_due(f: Facts) -> str | None:
    if f.closed or f.next_update_due is None:
        return None
    return f"The next stakeholder update is {_due(f.next_update_due, f.now)} ({f.update_rhythm})."


def concerns(f: Facts) -> list[str]:
    """What most needs attention, most pressing first. The order is a fixed rule: a regulator's deadline
    past or within a day, then overdue tasks, an overdue stakeholder update, blocked tasks, leads still
    open, and nobody in command."""
    now = f.now
    out: list[str] = []
    for d in sorted(f.deadlines, key=lambda d: d.due):
        if d.due <= now + SOON:
            out.append(f"Regulator deadline: {_deadline(d, now)}.")
    overdue = [t for t in _by_due(f.tasks, now) if t.due_at is not None and t.due_at <= now]
    out += [f"Overdue task: {_task(t, now)}." for t in overdue]
    if not f.closed and f.next_update_due is not None and f.next_update_due <= now:
        out.append(f"The stakeholder update is overdue since {when(f.next_update_due)}.")
    out += [f"Blocked task: {_task(t, now)}." for t in f.tasks if t.status is TaskStatus.BLOCKED]
    if f.leads:
        refs = ", ".join(x.ref for x in f.leads[:LISTED])
        out.append(
            f"{len(f.leads)} lead{'s' if len(f.leads) != 1 else ''} still open, not yet confirmed or "
            f"refuted: {refs}."
        )
    if f.commander is None and not f.closed:
        out.append(f"Nobody holds command of {f.ref}.")
    return out


def now_for(f: Facts) -> list[str]:
    """What the asker should do next: their own tasks by deadline, then what their role in the incident
    owes, then work waiting unclaimed in their team's queue."""
    now = f.now
    lines = [
        f"Your task: {_task(t, now, owner=False)}." for t in _by_due([t for t in f.tasks if t.mine], now)
    ]
    if f.asker_role is IncidentRole.COMMANDER or (f.asker_role is IncidentRole.COMMS):
        due = _update_due(f)
        if due:
            lines.append(due)
    if f.asker_role is IncidentRole.COMMANDER:
        lines += [
            f"Regulator deadline: {_deadline(d, now)}." for d in sorted(f.deadlines, key=lambda d: d.due)
        ]
        lines += [f"Unblock: {_task(t, now)}." for t in f.tasks if t.status is TaskStatus.BLOCKED]
        if f.leads:
            lines.append(
                f"Get {', '.join(x.ref for x in f.leads[:LISTED])} confirmed or refuted: "
                "leads decide the scope."
            )
    queue = [t for t in _by_due(f.tasks, now) if t.my_queue]
    lines += [f"Unclaimed in your team’s queue: {_task(t, now, owner=False)}." for t in queue]
    if not lines:
        if f.closed:
            return [f"{f.ref} is closed. Nothing is waiting on you."]
        ask = f"Ask {f.commander}, who holds command." if f.commander else "Nobody holds command yet."
        return [f"Nothing in {f.ref} is assigned to you or waiting in your team’s queue. {ask}"]
    return _capped(lines, "things for you")


def outstanding(f: Facts) -> list[str]:
    now = f.now
    overdue = sum(1 for t in f.tasks if t.due_at is not None and t.due_at <= now)
    blocked = sum(1 for t in f.tasks if t.status is TaskStatus.BLOCKED)
    head = f"{len(f.tasks)} open task{'s' if len(f.tasks) != 1 else ''}"
    detail = [
        w for w in (f"{overdue} overdue" if overdue else "", f"{blocked} blocked" if blocked else "") if w
    ]
    lines = [head + (f" ({', '.join(detail)})" if detail else "") + ("." if f.tasks else ".")]
    lines += _capped([_task(t, now) for t in _by_due(f.tasks, now)], "tasks")
    lines.append(
        f"{len(f.leads)} open lead{'s' if len(f.leads) != 1 else ''}"
        + (": " + ", ".join(f"{x.ref} {x.title}" for x in f.leads[:LISTED]) if f.leads else "")
        + "."
    )
    lines += [f"Regulator deadline: {_deadline(d, now)}." for d in sorted(f.deadlines, key=lambda d: d.due)]
    due = _update_due(f)
    if due:
        lines.append(due)
    if f.open_threads:
        lines.append(f"{f.open_threads} unresolved discussion thread{'s' if f.open_threads != 1 else ''}.")
    return lines


def last_update(f: Facts) -> list[str]:
    u = f.last_update
    if u is None:
        lines = [f"No stakeholder update has been sent for {f.ref} yet."]
    else:
        lines = [f"{u.ref}, update {u.number}, sent {when(u.sent_at)}:", *u.lines]
    due = _update_due(f)
    if due:
        lines.append(due)
    return lines


def leadership(f: Facts) -> list[str]:
    """The position in the order a senior reader wants it: where it stands, what's judged, what's
    known, what's next. Comms still writes and approves what's sent; this is what the record supports."""
    state = "closed" if f.closed else "open"
    lines = [
        f"{f.ref} {f.codename} ({f.title}) is {state}: {f.kind} in the {f.phase} phase, severity "
        f"{f.severity}, TLP:{f.tlp.upper()}."
    ]
    lines.append(
        f"Assessment: {f.assessment}." if f.assessment else "No current assessment has been made yet."
    )
    confirmed, leads = f.confirmed, f.leads
    lines.append(
        f"{len(confirmed)} confirmed finding{'s' if len(confirmed) != 1 else ''}"
        + (" (" + ", ".join(c.ref for c in confirmed[:LISTED]) + ")" if confirmed else "")
        + f" and {len(leads)} lead{'s' if len(leads) != 1 else ''} still being worked"
        + (" (" + ", ".join(x.ref for x in leads[:LISTED]) + ")" if leads else "")
        + "."
    )
    worries = concerns(f)
    if worries:
        lines.append(f"Main concern: {worries[0]}")
    due = _update_due(f)
    if due:
        lines.append(due)
    return lines


def bearings(f: Facts) -> list[Answer]:
    """Every standing question with its answer."""
    worries = concerns(f)
    concern = (
        [worries[0], *(f"Also: {w}" for w in worries[1:3])]
        if worries
        else ["Nothing is overdue, blocked, open as a lead or waiting on a regulator’s deadline."]
    )
    answers = {
        "now": now_for(f),
        "outstanding": outstanding(f),
        "last_update": last_update(f),
        "concern": concern,
        "leadership": leadership(f),
    }
    return [Answer(key, QUESTIONS[key], answers[key]) for key in QUESTIONS]


# ---- a free question ----------------------------------------------------------------------------------

PROMPT = (
    "You are Pnyx, the assistant inside Ostraka, an incident response platform. Someone working on {ref} "
    "has asked a question. Answer it from the record below and nothing else.\n"
    "Rules: cite the reference of every record you rely on, written as it appears (like FND-12, TSK-4, "
    "UPD-2). If the record doesn't answer the question, say so plainly in one sentence. Never call a lead "
    "confirmed: only findings marked confirmed are. Don't state how likely anything is unless you quote a "
    "current assessment. Don't use words of certainty such as definitely, clearly or proves. Bracketed "
    "placeholders like [ip address] are masked indicators: keep them as they are. Be brief: a few short "
    "sentences or a short list, in British English.\n\n"
    "The record:\n{record}\n\nThe question: {question}\n"
)


def record_text(f: Facts) -> str:
    """The record as the model reads it: the bearings, then the detail they're drawn from."""
    parts = [
        f"{f.ref} {f.codename}: {f.title}. {f.kind}, phase {f.phase}, severity {f.severity}, "
        f"TLP:{f.tlp.upper()}, {'closed' if f.closed else 'open'}. Commander: {f.commander or 'none'}. "
        f"Asked by {f.asker}" + (f", whose role here is {f.asker_role.value}." if f.asker_role else ".")
    ]
    for answer in bearings(f):
        parts += ["", answer.question, *(f"- {line}" for line in answer.lines)]
    parts += ["", "Findings:"]
    parts += [f"- {x.ref} ({x.status.value}) {x.title}: {x.statement}".rstrip(": ") for x in f.findings] or [
        "- none"
    ]
    parts += ["", "Recent activity, oldest first:", *(f"- {e}" for e in f.events)]
    return mask_text("\n".join(parts))


def prompt(f: Facts, question: str) -> str:
    return PROMPT.format(ref=f.ref, record=record_text(f), question=mask_text(question.strip()))


def known_refs(f: Facts) -> set[str]:
    return {m.upper() for m in REFS.findall(record_text(f))} | {f.ref.upper()}


def problems(f: Facts, answer: str) -> list[str]:
    """Why an answer can't be shown; empty when it can. Fixed rules, no model."""
    out = claims.problems(answer, known_refs(f), {x.ref.upper(): "lead" for x in f.leads})
    if not answer.strip():
        out.append("It came back empty.")
    return out


# ---- reading the record -------------------------------------------------------------------------------


def _owner(task: Task) -> str:
    if task.assignee is not None:
        return task.assignee.name
    if task.team is not None:
        return f"{task.team.name} queue, unclaimed"
    return "nobody"


async def gather(db: AsyncSession, incident: Incident, asker: User, now: datetime) -> Facts:
    """The snapshot, read once. Text is left unmasked here; the API masks it for the asker, and the
    model always gets it masked."""
    tasks = (
        await db.scalars(
            select(Task)
            .where(Task.incident_id == incident.id, Task.status.not_in(FINISHED))
            .order_by(Task.seq)
        )
    ).all()
    findings = (
        await db.scalars(
            select(Finding)
            .where(Finding.incident_id == incident.id, Finding.status != FindingStatus.REFUTED)
            .order_by(Finding.seq)
        )
    ).all()
    deadlines: list[DeadlineFact] = []
    clocks = (
        await db.scalars(
            select(Clock)
            .where(Clock.incident_id == incident.id, Clock.closed_at.is_(None))
            .order_by(Clock.seq)
        )
    ).all()
    for clock in clocks:
        met = {m.key for m in clock.marks if m.met_at is not None}
        name = clock.title or regimes.REGIMES[clock.regime.value].name
        for due in service.dues(clock, incident):
            if due.key not in met and due.due is not None:
                deadlines.append(DeadlineFact(clock.ref, name, due.name, due.due))

    sent = await drafts.last_sent(db, incident.id)
    plan = await drafts.plan_of(db, incident.id)
    minutes = rhythm.every(incident.severity, plan)
    base = sent.approved_at if sent and sent.approved_at else incident.declared_at
    current = await drafts.latest_assessment(db, incident.id)
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
    events = (
        await db.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.incident_id == incident.id)
            .order_by(ActivityEvent.position.desc())
            .limit(CONTEXT_EVENTS)
        )
    ).all()
    role = next((m.role for m in incident.members if m.user_id == asker.id), None)
    commander = next((m.user.name for m in incident.members if m.role is IncidentRole.COMMANDER), None)
    update = None
    if sent is not None and sent.approved_at is not None:
        lines = drafts.lines_of(sent)
        update = UpdateFact(sent.ref, sent.number, sent.approved_at, tuple(drafts.texts_of(sent, lines)))
    return Facts(
        ref=incident.ref,
        title=incident.title,
        codename=incident.codename,
        kind=incident.kind.value,
        phase=incident.phase.value,
        severity=incident.severity.value,
        tlp=incident.tlp.value,
        closed=incident.closed_at is not None,
        now=now,
        asker=asker.name,
        asker_role=role,
        commander=commander,
        tasks=[
            TaskFact(
                ref=t.ref,
                title=t.title,
                owner=_owner(t),
                mine=t.assignee_id == asker.id,
                my_queue=t.assignee_id is None and t.team_id is not None and t.team_id == asker.team_id,
                status=t.status,
                due_at=t.due_at,
                blocked_reason=t.blocked_reason,
            )
            for t in tasks
        ],
        findings=[FindingFact(f"FND-{x.seq}", x.title, x.status, x.statement) for x in findings],
        deadlines=deadlines,
        last_update=update,
        update_rhythm=rhythm.words(minutes),
        next_update_due=rhythm.next_due(minutes, base),
        assessment=(
            f"ASM-{current.seq}, {yardstick.phrase(current.probability, current.judgement)}"
            if current
            else None
        ),
        open_threads=threads,
        events=[f"{e.at:%d %b %H:%M} {e.summary}" for e in reversed(events)],
    )


def asked(incident_id: uuid.UUID, user: User, provider: str) -> dict[str, str]:
    """What's logged about a question: who, where and which model; never the question or the answer."""
    return {"incident": str(incident_id), "user": str(user.id), "provider": provider}
