"""Detection-gap follow-through (docs/ROADMAP.md, "Features that set Ostraka apart", item 5).

When a finding is confirmed, each ATT&CK technique it names that this incident isn't tracking yet
is checked with Shikari. One Shikari has no detection for opens a gap, with a task in the detection
team's queue (the team the admin chose for Shikari). A gap only closes when Shikari answers that it
has a detection for the technique: asked from the incident page, or after Shikari's signed
`run.completed` webhook (with the `automation` feature). Nobody closes one by hand, and finishing
the task doesn't close it either. When Shikari closes a gap, its task is marked done.

Gap tracking is on once a Shikari server is connected. If Shikari can't be reached when a finding is
confirmed, the gap opens anyway: nothing is known to detect it, and it's checked again later.

For a TLP:RED or TLP:AMBER+STRICT incident the detection team isn't brought in automatically, since
that would show them the incident: the gap opens without a task, and someone who manages the
incident gives it to whoever should have it.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import busy, licence, notify
from ostraka.db import sessions
from ostraka.incidents import activity
from ostraka.integrations import configured, tlp_refusal
from ostraka.integrations.http import Refused, Unreachable
from ostraka.integrations.shikari import Covered, Shikari
from ostraka.log import get_logger
from ostraka.models import (
    FINISHED,
    DetectionGap,
    Finding,
    FindingStatus,
    GapStatus,
    Incident,
    IncidentTeam,
    Integration,
    Product,
    Task,
    TaskStatus,
    Team,
    User,
    utcnow,
)

log = get_logger(__name__)

_background: set[asyncio.Task] = set()


def attack_url(technique: str) -> str:
    return "https://attack.mitre.org/techniques/" + technique.replace(".", "/") + "/"


@dataclass
class Plan:
    """What confirming a finding will open, worked out (Shikari asked) before anything is locked."""

    techniques: list[str]
    coverage: dict[str, Covered] = field(default_factory=dict)
    error: str = ""


async def _tracked(db: AsyncSession, incident_id: uuid.UUID) -> set[str]:
    return set(
        (
            await db.scalars(select(DetectionGap.technique).where(DetectionGap.incident_id == incident_id))
        ).all()
    )


async def _ask(row: Integration, incident: Incident, techniques: list[str]) -> tuple[dict[str, Covered], str]:
    if refusal := tlp_refusal(Product.SHIKARI, incident, row):
        return {}, refusal
    try:
        return await Shikari.of(row).coverage(techniques), ""
    except (Unreachable, Refused) as exc:
        return {}, str(exc)[:300]


async def prepare(db: AsyncSession, incident: Incident, techniques: list[str]) -> Plan | None:
    """The techniques a confirmation adds, with Shikari's answer for them; None when there's
    nothing to do (no Shikari connected, or every technique already tracked)."""
    row = await configured(db, Product.SHIKARI)
    if row is None:
        return None
    new = sorted(set(techniques) - await _tracked(db, incident.id))
    if not new:
        return None
    coverage, error = await _ask(row, incident, new)
    return Plan(new, coverage, error)


async def open_gaps(db: AsyncSession, incident: Incident, finding: Finding, actor: User, plan: Plan) -> None:
    """Record the plan in the caller's transaction: a closed row for a technique Shikari already
    detects, an open gap (and its task) for each one it doesn't. The caller commits."""
    row = await db.get(Integration, Product.SHIKARI)
    team = await db.get(Team, row.detection_team_id) if row and row.detection_team_id else None
    now = utcnow()
    for technique in plan.techniques:
        answer = plan.coverage.get(technique)
        gap = DetectionGap(
            id=uuid.uuid4(),
            incident_id=incident.id,
            technique=technique,
            technique_name=answer.name if answer else "",
            finding_id=finding.id,
            opened_by=actor.id,
            opened_at=now,
            checked_at=now if answer else None,
            check_error="" if answer else plan.error,
            status=GapStatus.OPEN,
            closed_by="",
        )
        db.add(gap)
        if answer and answer.covered:
            gap.status, gap.closed_at = GapStatus.CLOSED, now
            gap.closed_by = f"Shikari already had a detection when FND-{finding.seq} was confirmed"
            continue
        await activity.record(
            db,
            incident,
            actor,
            "detection_gap_opened",
            f"Confirming FND-{finding.seq} opened a detection gap for {technique}",
            entity_type="detection_gap",
            entity_id=gap.id,
            technique=technique,
        )
        if team is not None and not notify.restricted(incident):
            await _give_task(db, incident, gap, finding, actor, team)


async def _give_task(
    db: AsyncSession, incident: Incident, gap: DetectionGap, finding: Finding, actor: User, team: Team
) -> None:
    """The gap's task, in the detection team's queue, bringing the team in if it isn't yet: it's
    Ostraka's rule that does this, so it doesn't need the confirming analyst to manage the incident."""
    from ostraka import work

    if not any(t.team_id == team.id for t in incident.teams):
        incident.teams.append(IncidentTeam(team_id=team.id))
        await activity.record(
            db,
            incident,
            None,
            "team_added",
            f"Ostraka involved {team.name} for detection follow-ups",
            entity_type="team",
            entity_id=team.id,
        )
    name = f" ({gap.technique_name})" if gap.technique_name else ""
    task = Task(
        incident_id=incident.id,
        title=f"Detection for {gap.technique}{name}"[:200],
        description=(
            f"FND-{finding.seq} was confirmed with {gap.technique}, and Shikari has no detection for it. "
            "Build one in Shikari: the gap closes when Shikari has it.\n\n" + attack_url(gap.technique)
        ),
        team_id=team.id,
        created_by=actor.id,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task, ["team", "assignee"])
    gap.task_id = task.id
    await activity.record(
        db,
        incident,
        actor,
        "task_added",
        f"Ostraka added {task.ref} for {team.name} to follow up the detection gap for {gap.technique}",
        entity_type="task",
        entity_id=task.id,
    )
    await work.announce(db, incident, task, actor)


async def catch_up(db: AsyncSession, incident: Incident, actor: User) -> Plan | None:
    """Confirmed findings whose techniques aren't tracked yet (confirmed before Shikari was
    connected, or edited since): opened now, each against the first finding that names it."""
    findings = (
        await db.scalars(
            select(Finding)
            .where(Finding.incident_id == incident.id, Finding.status == FindingStatus.CONFIRMED)
            .order_by(Finding.decided_at)
        )
    ).all()
    tracked = await _tracked(db, incident.id)
    by_finding: dict[uuid.UUID, list[str]] = {}
    first: dict[str, Finding] = {}
    for finding in findings:
        for technique in finding.techniques or []:
            if technique not in tracked and technique not in first:
                first[technique] = finding
                by_finding.setdefault(finding.id, []).append(technique)
    if not first:
        return None
    plan = await prepare(db, incident, list(first))
    if plan is None:
        return None
    for finding in findings:
        if finding.id in by_finding:
            await open_gaps(
                db, incident, finding, actor, Plan(by_finding[finding.id], plan.coverage, plan.error)
            )
    return plan


@dataclass
class Checked:
    checked: int = 0
    closed: int = 0
    error: str = ""


async def recheck(db: AsyncSession, gaps: list[DetectionGap], how: str) -> Checked:
    """Ask Shikari about open gaps and close those it now detects. The caller commits with
    `activity.finish`. A gap of a restricted incident is only asked about on your own network."""
    row = await configured(db, Product.SHIKARI)
    result = Checked()
    if row is None or not gaps:
        if row is None:
            result.error = "No Shikari server is connected."
        return result
    incidents = {g.incident_id: await db.get(Incident, g.incident_id) for g in gaps}
    allowed = [g for g in gaps if tlp_refusal(Product.SHIKARI, incidents[g.incident_id], row) is None]
    for gap in gaps:
        if gap not in allowed:
            gap.check_error = tlp_refusal(Product.SHIKARI, incidents[gap.incident_id], row) or ""
            result.error = gap.check_error
    if not allowed:
        return result
    try:
        coverage = await Shikari.of(row).coverage([g.technique for g in allowed])
    except (Unreachable, Refused) as exc:
        result.error = str(exc)[:300]
        for gap in allowed:
            gap.check_error = result.error
        return result
    now = utcnow()
    for gap in allowed:
        result.checked += 1
        answer = coverage.get(gap.technique)
        gap.checked_at, gap.check_error = now, ""
        if answer and answer.name:
            gap.technique_name = answer.name
        if answer and answer.covered:
            await _close(db, incidents[gap.incident_id], gap, how)
            result.closed += 1
    return result


async def _close(db: AsyncSession, incident: Incident, gap: DetectionGap, how: str) -> None:
    gap.status, gap.closed_at, gap.closed_by = GapStatus.CLOSED, utcnow(), how[:200]
    task = await db.get(Task, gap.task_id) if gap.task_id else None
    finished = ""
    if task is not None and task.status not in FINISHED:
        task.status, task.completed_at, task.completed_by = TaskStatus.DONE, utcnow(), None
        task.updated_at = utcnow()
        task.version += 1
        finished = f"; {task.ref} is done"
    await activity.record(
        db,
        incident,
        None,
        "detection_gap_closed",
        f"Shikari has a detection for {gap.technique}: the gap is closed{finished}",
        entity_type="detection_gap",
        entity_id=gap.id,
        technique=gap.technique,
        how=how[:200],
    )
    if finished:
        await activity.record(
            db,
            incident,
            None,
            "task_updated",
            f"Ostraka finished {task.ref}: Shikari has a detection for {gap.technique}",
            entity_type="task",
            entity_id=task.id,
        )


async def open_gaps_everywhere(db: AsyncSession) -> list[DetectionGap]:
    return list((await db.scalars(select(DetectionGap).where(DetectionGap.status == GapStatus.OPEN))).all())


def queue_recheck(how: str) -> None:
    """After Shikari's webhook: ask about every open gap, in the background."""
    job = asyncio.create_task(_recheck_all(how))
    _background.add(job)
    job.add_done_callback(_background.discard)


async def _recheck_all(how: str) -> None:
    try:
        async with sessions()() as db:
            if not (await licence.current(db)).has(licence.AUTOMATION):
                return
            await recheck(db, await open_gaps_everywhere(db), how)
            await activity.finish(db)
    except Exception:
        log.exception("detection gap recheck failed")


async def wait() -> None:
    """Let a background recheck finish (shutdown and tests)."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


def startup() -> None:
    busy.register("detection-gaps-checking", lambda: bool(_background))
