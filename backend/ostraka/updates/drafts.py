"""Building, checking and sending stakeholder updates.

A draft covers the activity log from the last update sent (or the declaration) to the moment it's
built. The log says which findings, assets, tasks and phase moves to look at; each update also keeps
a snapshot of asset and task states, so a change is reported once, when it happens.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import notify
from ostraka.api.assessments import basis_findings, saved_draft
from ostraka.incidents import access, activity
from ostraka.models import (
    FINISHED,
    ActivityEvent,
    Assessment,
    AssessmentStatus,
    Asset,
    Finding,
    FindingStatus,
    Incident,
    IncidentRole,
    IncidentStatus,
    NotificationKind,
    RecordKind,
    Stakeholder,
    StakeholderUpdate,
    Task,
    TaskStatus,
    UpdatePlan,
    UpdateRevision,
    UpdateStatus,
    User,
    utcnow,
)
from ostraka.standard import update as standard
from ostraka.standard.assessment import Draft, sentences
from ostraka.updates import rhythm

# What the activity log is read for; anything else (comments, observables, links) isn't an update's news
RELEVANT = frozenset(
    {
        "finding_added",
        "finding_confirmed",
        "finding_refuted",
        "finding_lead",
        "asset_added",
        "asset_updated",
        "incident_updated",
        "task_updated",
        "task_decided",
        "assessment_made",
    }
)


# ---- reading the record ------------------------------------------------------------------------------


async def last_sent(db: AsyncSession, incident_id: uuid.UUID) -> StakeholderUpdate | None:
    return await db.scalar(
        select(StakeholderUpdate)
        .where(StakeholderUpdate.incident_id == incident_id, StakeholderUpdate.status == UpdateStatus.SENT)
        .order_by(StakeholderUpdate.number.desc())
        .limit(1)
    )


async def open_draft(db: AsyncSession, incident_id: uuid.UUID) -> StakeholderUpdate | None:
    return await db.scalar(
        select(StakeholderUpdate).where(
            StakeholderUpdate.incident_id == incident_id, StakeholderUpdate.status == UpdateStatus.DRAFT
        )
    )


async def plan_of(db: AsyncSession, incident_id: uuid.UUID) -> UpdatePlan | None:
    return await db.get(UpdatePlan, incident_id)


async def latest_assessment(db: AsyncSession, incident_id: uuid.UUID) -> Assessment | None:
    return await db.scalar(
        select(Assessment)
        .where(Assessment.incident_id == incident_id, Assessment.status == AssessmentStatus.CURRENT)
        .order_by(Assessment.created_at.desc(), Assessment.seq.desc())
        .limit(1)
    )


async def assessment_draft(db: AsyncSession, assessment: Assessment | None) -> Draft | None:
    if assessment is None:
        return None
    return saved_draft(assessment, await basis_findings(db, assessment))


async def _events(
    db: AsyncSession, incident: Incident, since: datetime, until: datetime
) -> list[ActivityEvent]:
    return list(
        (
            await db.scalars(
                select(ActivityEvent)
                .where(
                    ActivityEvent.incident_id == incident.id,
                    ActivityEvent.at > since,
                    ActivityEvent.at <= until,
                    ActivityEvent.action.in_(RELEVANT),
                )
                .order_by(ActivityEvent.position)
            )
        ).all()
    )


async def anything_since(db: AsyncSession, incident: Incident, since: datetime) -> bool:
    """Whether the log has news since `since`: what an on-change-only incident waits for."""
    return bool(
        await db.scalar(
            select(func.count())
            .select_from(ActivityEvent)
            .where(
                ActivityEvent.incident_id == incident.id,
                ActivityEvent.at > since,
                ActivityEvent.action.in_(RELEVANT),
            )
        )
    )


async def _of(db: AsyncSession, model, ids: set[uuid.UUID]) -> list:
    if not ids:
        return []
    return list((await db.scalars(select(model).where(model.id.in_(ids)).order_by(model.seq))).all())


def _in(moment: datetime | None, since: datetime, until: datetime) -> bool:
    return moment is not None and since < moment <= until


async def build(
    db: AsyncSession, incident: Incident, since: datetime, until: datetime, previous: StakeholderUpdate | None
) -> tuple[dict, Assessment | None]:
    """The content of a draft covering since..until: its composition and the state snapshot."""
    events = await _events(db, incident, since, until)
    touched: dict[str, set[uuid.UUID]] = {"finding": set(), "asset": set(), "task": set()}
    reopened: set[uuid.UUID] = set()
    changes: list[standard.Change] = []
    for e in events:
        if e.entity_type in touched and e.entity_id:
            touched[e.entity_type].add(e.entity_id)
        if e.action == "finding_lead" and e.entity_id:
            reopened.add(e.entity_id)
        if e.action == "incident_updated":
            moved = (e.data or {}).get("changes", {})
            if moved.get("phase"):
                changes.append(standard.Change("phase", "", detail=moved["phase"]))
            if moved.get("severity"):
                changes.append(standard.Change("severity", "", detail=f"now {moved['severity']}"))

    before = (previous.content or {}).get("snapshot", {}) if previous else {}
    for f in await _of(db, Finding, touched["finding"]):
        ref = f"FND-{f.seq}"
        if f.status is FindingStatus.CONFIRMED and _in(f.decided_at, since, until):
            changes.append(standard.Change("confirmed", ref, f.title))
        elif f.status is FindingStatus.REFUTED and _in(f.decided_at, since, until):
            changes.append(standard.Change("refuted", ref, f.title))
        elif f.status is FindingStatus.LEAD and (_in(f.created_at, since, until) or f.id in reopened):
            changes.append(standard.Change("lead", ref, f.title))

    was_assets = before.get("assets", {})
    for a in await _of(db, Asset, touched["asset"]):
        status = a.status.value
        if status in standard.CONTAINED and was_assets.get(str(a.id)) != status:
            changes.append(standard.Change("containment", f"AST-{a.seq}", a.hostname, status))

    was_tasks = before.get("tasks", {})
    for t in await _of(db, Task, touched["task"]):
        if t.status is TaskStatus.DONE and _in(t.completed_at, since, until):
            changes.append(standard.Change("task_done", t.ref, t.title, t.outcome or ""))
        elif t.status is TaskStatus.BLOCKED and was_tasks.get(str(t.id)) != TaskStatus.BLOCKED.value:
            changes.append(standard.Change("task_blocked", t.ref, t.title, t.blocked_reason))

    assets = list((await db.scalars(select(Asset).where(Asset.incident_id == incident.id))).all())
    tasks = list((await db.scalars(select(Task).where(Task.incident_id == incident.id))).all())
    position = standard.Position(
        phase=incident.phase.value,
        severity=incident.severity.value,
        assets=len(assets),
        contained=sum(a.status.value in standard.CONTAINED for a in assets),
        tasks_open=sum(t.status not in FINISHED for t in tasks),
        tasks_blocked=sum(t.status is TaskStatus.BLOCKED for t in tasks),
    )
    assessment = await latest_assessment(db, incident.id)
    draft = await assessment_draft(db, assessment)
    plan = await plan_of(db, incident.id)
    content = {
        "assessment_ref": f"ASM-{assessment.seq}" if assessment else None,
        "assessment_sentences": sentences(draft) if draft else [],
        "assessment_new": bool(assessment and assessment.created_at > since) or previous is None,
        "changes": [{"kind": c.kind, "ref": c.ref, "title": c.title, "detail": c.detail} for c in changes],
        "position": position.__dict__,
        "next_line": rhythm.next_line(rhythm.every(incident.severity, plan)),
        "previous": previous.ref if previous else None,
        "snapshot": {
            "assets": {str(a.id): a.status.value for a in assets},
            "tasks": {str(t.id): t.status.value for t in tasks},
        },
    }
    return content, assessment


def composition(update: StakeholderUpdate) -> standard.Composition:
    c = update.content or {}
    return standard.Composition(
        assessment_ref=c.get("assessment_ref"),
        assessment_sentences=tuple(c.get("assessment_sentences", [])),
        assessment_new=c.get("assessment_new", False),
        changes=tuple(standard.Change(**ch) for ch in c.get("changes", [])),
        position=standard.Position(**c.get("position", {"phase": "", "severity": ""})),
        next_line=c.get("next_line", ""),
        previous=c.get("previous"),
        note=update.note,
    )


def lines_of(update: StakeholderUpdate) -> list[standard.Line]:
    """The update's lines: as sent, for a sent update; as composed now, for a draft."""
    if update.status is UpdateStatus.SENT and "sent" in (update.content or {}):
        return [standard.Line(**line) for line in update.content["sent"]["lines"]]
    return standard.compose(composition(update))


def texts_of(update: StakeholderUpdate, lines: list[standard.Line]) -> list[str]:
    """The words that go out: the accepted rewording if comms chose it, otherwise Ostraka's own."""
    if update.status is UpdateStatus.SENT and "sent" in (update.content or {}):
        return list(update.content["sent"]["texts"])
    if update.use_rephrased and update.rephrased and len(update.rephrased) == len(lines):
        return list(update.rephrased)
    return [line.text for line in lines]


async def check(db: AsyncSession, update: StakeholderUpdate) -> standard.Check:
    assessment = await db.get(Assessment, update.assessment_id) if update.assessment_id else None
    return standard.check(
        composition(update),
        standard.compose(composition(update)),
        await assessment_draft(db, assessment),
        superseded=assessment is not None and assessment.status is AssessmentStatus.SUPERSEDED,
        rephrased=update.rephrased,
        use_rephrased=update.use_rephrased,
    )


def revise(update: StakeholderUpdate, change: str, by: User | None) -> None:
    """Move a draft to its next version, keeping the one before as a revision."""
    lines = standard.compose(composition(update))
    update.version += 1
    update.revisions.append(
        UpdateRevision(
            version=update.version,
            change=change,
            lines=texts_of(update, lines),
            by=by.id if by else None,
            at=utcnow(),
        )
    )


# ---- drafting ----------------------------------------------------------------------------------------


async def make_draft(db: AsyncSession, incident: Incident, by: User | None) -> StakeholderUpdate:
    """A new draft from what changed since the last update sent."""
    previous = await last_sent(db, incident.id)
    since = previous.until if previous else incident.declared_at
    now = utcnow()
    content, assessment = await build(db, incident, since, now, previous)
    number = (
        await db.scalar(
            select(func.coalesce(func.max(StakeholderUpdate.number), 0)).where(
                StakeholderUpdate.incident_id == incident.id
            )
        )
    ) + 1
    update = StakeholderUpdate(
        incident_id=incident.id,
        number=number,
        since=since,
        until=now,
        assessment_id=assessment.id if assessment else None,
        content=content,
        drafted_by=by.id if by else None,
        drafted_at=now,
        status=UpdateStatus.DRAFT,
        note="",
        rephrased_by="",
        use_rephrased=False,
        recipients=[],
        version=1,
    )
    update.revisions = [
        UpdateRevision(
            version=1,
            change="drafted",
            lines=[line.text for line in standard.compose(composition(update))],
            by=by.id if by else None,
            at=now,
        )
    ]
    db.add(update)
    await db.flush()
    who = by.name if by else "Ostraka"
    await activity.record(
        db,
        incident,
        by,
        "update_drafted",
        f"{who} drafted stakeholder update {number} ({update.ref})",
        entity_type="update",
        entity_id=update.id,
        number=number,
    )
    return update


async def rebuild(db: AsyncSession, incident: Incident, update: StakeholderUpdate, by: User) -> None:
    """Bring a draft up to now. Comms' note stays; a rewording of the old text doesn't."""
    previous = await last_sent(db, incident.id)
    now = utcnow()
    content, assessment = await build(db, incident, update.since, now, previous)
    update.content, update.until = content, now
    update.assessment_id = assessment.id if assessment else None
    update.rephrased, update.rephrased_by, update.use_rephrased = None, "", False
    revise(update, "rebuilt", by)


# ---- who hears about it --------------------------------------------------------------------------------


def comms_of(incident: Incident) -> list[uuid.UUID]:
    return [m.user_id for m in incident.members if m.role is IncidentRole.COMMS]


async def nudge(db: AsyncSession, incident: Incident, update: StakeholderUpdate, why: str) -> None:
    """Tell comms (or, with nobody in comms, the commander) that a draft is waiting."""
    ids = comms_of(incident) or [access.commander_id(incident)]
    people = [u for u in [await db.get(User, i) for i in ids if i] if u]
    notify.send(
        db,
        people,
        NotificationKind.UPDATE_DUE,
        f"Stakeholder update {update.number} for {incident.ref} is ready to check: {incident.title}",
        body=why,
        link=notify.incident_link(incident, "updates", update.ref),
        incident=incident,
    )


async def send(db: AsyncSession, incident: Incident, update: StakeholderUpdate, by: User) -> list[dict]:
    """Freeze the update and send it to every stakeholder. Everything outside Ostraka goes through
    `notify.outward`. Returns who it went to, by which channel."""
    lines = standard.compose(composition(update))
    texts = texts_of(update, lines)
    content = dict(update.content)
    content["sent"] = {"lines": [line.json() for line in lines], "texts": texts}
    update.content = content
    update.status = UpdateStatus.SENT
    update.approved_by, update.approved_at = by.id, utcnow()
    revise(update, "sent", by)

    title = f"{incident.ref} update {update.number}: {incident.title}"
    body = standard.render(lines, texts)
    if update.use_rephrased:
        body += "\n\nWorded with AI help from Ostraka's draft, and checked against it before approval."
    link = notify.incident_link(incident, "updates", update.ref)
    sent: list[dict] = []
    stakeholders = (await db.scalars(select(Stakeholder).where(Stakeholder.incident_id == incident.id))).all()
    for s in stakeholders:
        if s.user is not None:
            made = notify.send(
                db,
                [s.user],
                NotificationKind.STAKEHOLDER_UPDATE,
                title,
                body=body,
                link=link,
                incident=incident,
                actor=None,
            )
            sent.append({"to": s.user.name, "channel": "ostraka", "sent": bool(made)})
        elif s.team is not None:
            ok = notify.team_channel(
                db,
                s.team,
                NotificationKind.STAKEHOLDER_UPDATE,
                title,
                body=body,
                link=link,
                incident=incident,
            )
            sent.append({"to": f"{s.team.name} (Teams)", "channel": "teams", "sent": ok})
        elif s.email:
            ok = notify.email_outside(
                db, s.email, NotificationKind.STAKEHOLDER_UPDATE, title, body=body, incident=incident
            )
            sent.append({"to": s.name or s.email, "channel": "email", "sent": ok})
    update.recipients = sent
    return sent


# ---- the scheduler -----------------------------------------------------------------------------------


async def tick(db: AsyncSession, now: datetime) -> int:
    """Draft each open incident's update when it falls due, and nudge comms. Called from
    `ostraka.scheduler.tick` inside its advisory lock; the caller commits."""
    acted = 0
    incidents = (
        await db.scalars(
            select(Incident).where(
                Incident.status == IncidentStatus.OPEN, Incident.kind == RecordKind.INCIDENT
            )
        )
    ).all()
    for incident in incidents:
        plan = await plan_of(db, incident.id)
        minutes = rhythm.every(incident.severity, plan)
        previous = await last_sent(db, incident.id)
        base = previous.approved_at if previous and previous.approved_at else incident.declared_at
        draft = await open_draft(db, incident.id)
        if minutes is not None:
            due = rhythm.next_due(minutes, base)
            if due is None or now < due or (plan is not None and plan.nudged_for == due):
                continue
            why = (
                f"Updates on {incident.ref} go {rhythm.words(minutes)}; "
                f"this one was due {due:%d %b %H:%M} UTC."
            )
        else:
            # On change only: news since the last update sent, and since the last draft anyone saw
            seen = await db.scalar(
                select(func.max(StakeholderUpdate.drafted_at)).where(
                    StakeholderUpdate.incident_id == incident.id
                )
            )
            since = max(t for t in (base, seen) if t is not None)
            if draft is not None or not await anything_since(db, incident, since):
                continue
            due = now
            why = f"Updates on {incident.ref} go on change only, and something has changed."
        if draft is None:
            draft = await make_draft(db, incident, None)
        await nudge(db, incident, draft, why)
        if plan is None:
            plan = UpdatePlan(incident_id=incident.id, rhythm_set=False, version=1)
            db.add(plan)
        plan.nudged_for = due
        acted += 1
    return acted


async def sent_updates(db: AsyncSession, incident_id: uuid.UUID) -> list[tuple]:
    """For the briefing report: each update as sent, oldest first."""
    rows = (
        await db.scalars(
            select(StakeholderUpdate)
            .where(
                StakeholderUpdate.incident_id == incident_id, StakeholderUpdate.status == UpdateStatus.SENT
            )
            .order_by(StakeholderUpdate.number)
        )
    ).all()
    approvers = {r.approved_by for r in rows if r.approved_by}
    names = (
        dict((await db.execute(select(User.id, User.name).where(User.id.in_(approvers)))).all())
        if approvers
        else {}
    )
    out = []
    for u in rows:
        lines = lines_of(u)
        by = names.get(u.approved_by, "Former member")
        out.append((u.ref, u.number, u.approved_at, by, texts_of(u, lines), u.use_rephrased))
    return out
