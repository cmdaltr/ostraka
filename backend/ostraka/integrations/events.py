"""What Ostraka does with Rowanholt's and Shikari's webhooks, once their signatures have checked out.

Each is handled in the background with its own session, as the detection-gap recheck is, so the sender
gets its 202 at once. Everything written goes in the records' activity logs with no actor: Rowanholt or
Shikari said so, not a person.

Rowanholt (`job.completed`, `job.failed`, `finding.confirmed`): the records concerned are the one named
by `external_ref` and any linked to the job or its case. A finished job updates their links and is
noted; a finding confirmed in Rowanholt comes in as a lead citing its row, once per record, as an
import would bring it.

Shikari (`run.completed`, `run.failed`, `run.executed`): the run is read again with `GET /api/runs/{id}`
and its snapshot (detections and counts, never rows) is updated, noting each change of status.

The TLP guard applies as it does from an incident page: a restricted record only hears from a server on
the organisation's own network.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import busy
from ostraka.db import sessions
from ostraka.incidents import activity
from ostraka.incidents.refs import parse_ref
from ostraka.integrations import configured, tlp_refusal
from ostraka.integrations.http import Refused, Unreachable
from ostraka.integrations.rowanholt import FINDING_EVENTS, JOB_EVENTS, Rowanholt
from ostraka.integrations.shikari import RUN_EVENTS, Shikari
from ostraka.log import get_logger
from ostraka.models import ArchiveState, Incident, Product, RowanholtCitation, RowanholtLink, ShikariRun

log = get_logger(__name__)
_background: set[asyncio.Task] = set()


def _queue(coro) -> None:
    job = asyncio.create_task(coro)
    _background.add(job)
    job.add_done_callback(_background.discard)


def rowanholt(event: str, data: dict[str, Any]) -> bool:
    """Act on a Rowanholt event in the background. False when Ostraka doesn't act on it."""
    if event not in JOB_EVENTS | FINDING_EVENTS or not str(data.get("job_id") or ""):
        return False
    _queue(_rowanholt(event, data))
    return True


def shikari(event: str, data: dict[str, Any]) -> bool:
    """Act on a Shikari run event in the background. False when it isn't about a run Ostraka started."""
    if event not in RUN_EVENTS or not str(data.get("run_id") or ""):
        return False
    _queue(_shikari(str(data["run_id"])[:64]))
    return True


async def wait() -> None:
    """Let handling in flight finish (shutdown and tests)."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


def startup() -> None:
    busy.register("suite-webhooks", lambda: bool(_background))


async def records_for(db: AsyncSession, job_id: str, case_number: str, external_ref: str) -> list[Incident]:
    """The records a Rowanholt job concerns: the one it was started for, and any linked to it or its case,
    while they aren't archived."""
    found: dict[uuid.UUID, Incident] = {}
    ref = parse_ref(external_ref) if external_ref else None
    if ref is not None and ref.kind == "incident":
        from ostraka.api.incidents import find_by_ref  # the API's lookup; imported late, it imports us

        if (incident := await find_by_ref(db, ref)) is not None:
            found[incident.id] = incident
    clauses = [RowanholtLink.job_id == job_id]
    if case_number:
        clauses.append((RowanholtLink.job_id == "") & (RowanholtLink.case_number == case_number))
    for link in (await db.scalars(select(RowanholtLink).where(or_(*clauses)))).all():
        if link.incident_id not in found and (incident := await db.get(Incident, link.incident_id)):
            found[incident.id] = incident
    # an archived record takes nothing new until it's restored
    return [i for i in found.values() if i.archive_state is ArchiveState.ACTIVE]


async def _rowanholt(event: str, data: dict[str, Any]) -> None:
    job_id = str(data.get("job_id") or "")[:64]
    case_number = str(data.get("case_number") or "")[:200]
    try:
        async with sessions()() as db:
            row = await configured(db, Product.ROWANHOLT)
            if row is None:
                return
            client = Rowanholt.of(row)
            records = [
                r
                for r in await records_for(db, job_id, case_number, str(data.get("external_ref") or ""))
                if tlp_refusal(Product.ROWANHOLT, r, row) is None
            ]
            if not records:
                return
            if event in JOB_EVENTS:
                await _job_finished(db, records, job_id, case_number, event, data)
            else:
                await _finding_confirmed(db, client, records, job_id, case_number, data)
            await activity.finish(db)
    except Exception:
        log.exception("acting on Rowanholt's %s failed", event)


async def _job_finished(
    db: AsyncSession, records: list[Incident], job_id: str, case_number: str, event: str, data: dict
) -> None:
    status = "completed" if event == "job.completed" else "failed"
    for incident in records:
        links = (
            await db.scalars(
                select(RowanholtLink).where(
                    RowanholtLink.incident_id == incident.id, RowanholtLink.job_id == job_id
                )
            )
        ).all()
        for link in links:
            link.status, link.progress = status, 100 if status == "completed" else link.progress
        why = f": {str(data.get('error') or '')[:200]}" if status == "failed" and data.get("error") else ""
        await activity.record(
            db,
            incident,
            None,
            "rowanholt_job_finished",
            f"Rowanholt job {job_id}{f' (case {case_number})' if case_number else ''} {status}{why}",
            entity_type="rowanholt",
            status=status,
            job=job_id,
        )


async def _finding_confirmed(
    db: AsyncSession, client: Rowanholt, records: list[Incident], job_id: str, case_number: str, data: dict
) -> None:
    from ostraka.api.rowanholt import bring_in  # imported late: the API imports this module's package

    finding_id = str(data.get("finding_id") or "")
    try:
        remote = next((f for f in await client.findings(job_id) if str(f.get("id")) == finding_id), None)
    except (Unreachable, Refused) as exc:
        log.info("Rowanholt's finding %s:%s couldn't be read: %s", job_id, finding_id, exc)
        return
    if remote is None:
        return
    key = f"{job_id}:{finding_id}"
    for incident in records:
        already = await db.scalar(
            select(RowanholtCitation.id).where(
                RowanholtCitation.incident_id == incident.id, RowanholtCitation.remote_finding_id == key
            )
        )
        if already is None:
            await bring_in(db, incident, None, job_id, case_number, remote)


def _said(run: ShikariRun) -> str:
    totals = run.totals or {}
    if run.status == "executed":
        return (
            f"ran {totals.get('run', 0)} of {totals.get('queries', 0)} queries: "
            f"{totals.get('hits', 0)} results"
            + (f", {totals['errors']} failed" if totals.get("errors") else "")
            + (f", {totals['skipped']} had no SIEM connection" if totals.get("skipped") else "")
        )
    if run.status == "failed":
        return f"failed{': ' + run.error if run.error else ''}"
    found = len(run.detections or [])
    return f"is {run.status}" + (f", {found} detection(s)" if found else "")


async def _shikari(run_id: str) -> None:
    from ostraka.api.shikari_runs import apply

    try:
        async with sessions()() as db:
            run = await db.scalar(select(ShikariRun).where(ShikariRun.run_id == run_id))
            row = await configured(db, Product.SHIKARI)
            if run is None or row is None:
                return
            incident = await db.get(Incident, run.incident_id)
            if incident is None or tlp_refusal(Product.SHIKARI, incident, row):
                return
            try:
                answer = await Shikari.of(row).run(run_id)
            except (Unreachable, Refused) as exc:
                log.info("Shikari's run %s couldn't be read: %s", run_id, exc)
                return
            if apply(run, answer):
                await activity.record(
                    db,
                    incident,
                    None,
                    "shikari_run_updated",
                    f"Shikari's hunt for {run.kind} {run.target} {_said(run)}",
                    entity_type="shikari_run",
                    entity_id=run.id,
                    status=run.status,
                )
            await activity.finish(db)
    except Exception:
        log.exception("acting on Shikari's run %s failed", run_id)
