"""Shikari hunt runs on a record: start one, list them, and fetch where one has got to.

A run is Shikari's (`POST /api/runs` with the record's reference); Ostraka keeps a snapshot of its
detections and, when its queries were run against Shikari's SIEM connections, how many results each
found. Never a result row: Shikari doesn't return them, and Ostraka has nowhere to put them. Shikari's
`run.*` webhooks refresh the snapshot (`integrations.events`); Refresh asks without waiting for one.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incidents import access, activity, views
from ostraka.integration_schemas import RunCount, ShikariRunIn, ShikariRunOut
from ostraka.integrations import configured, tlp_refusal
from ostraka.integrations.http import Refused, Unreachable
from ostraka.integrations.shikari import Shikari
from ostraka.models import Incident, Product, ShikariRun, User, utcnow

router = APIRouter(prefix="/api/incidents/{incident_id}/shikari", tags=["shikari"])

NOT_CONNECTED = "No Shikari server is connected. An admin pairs one under Settings → Integrations."
MAX_DETECTIONS = 500


async def server(db: AsyncSession, incident: Incident) -> Shikari:
    row = await configured(db, Product.SHIKARI)
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_CONNECTED)
    if refusal := tlp_refusal(Product.SHIKARI, incident, row):
        raise HTTPException(status.HTTP_403_FORBIDDEN, refusal)
    return Shikari.of(row)


def _count(item: dict) -> dict:
    """One query's count as Ostraka keeps it: a number, an error or skipped, and nothing else."""
    hits = item.get("hits")
    return RunCount(
        file=str(item.get("file") or "")[:300],
        technique=str(item.get("technique") or "")[:16],
        platform=str(item.get("platform") or "")[:32],
        query_index=int(item.get("query_index") or 0),
        hits=hits if isinstance(hits, int) and not isinstance(hits, bool) and hits >= 0 else None,
        error=str(item.get("error") or "")[:300],
        skipped=item.get("skipped") is True,
    ).model_dump()


def _detection(item: dict) -> dict:
    return {
        "file": str(item.get("file") or "")[:300],
        "name": str(item.get("name") or "")[:300],
        "techniques": [str(t)[:16] for t in item.get("techniques") or []][:20],
        "platforms": [str(p)[:32] for p in item.get("platforms") or []][:8],
        "queries": [
            {"platform": str(q.get("platform") or "")[:32], "query": str(q.get("query") or "")[:20000]}
            for q in item.get("queries") or []
            if isinstance(q, dict)
        ][:8],
    }


def apply(run: ShikariRun, answer: dict) -> bool:
    """Take Shikari's view of a run (`GET /api/runs/{id}`) into the snapshot. Returns whether its status
    changed. Pure apart from the row it changes; anything but counts and detections is ignored."""
    before = run.status
    run.status = str(answer.get("status") or run.status)[:16]
    run.group_name = str(answer.get("group_name") or run.group_name)[:200]
    run.error = str(answer.get("error") or "")[:300]
    detections = answer.get("detections")
    if isinstance(detections, list):
        run.detections = [_detection(d) for d in detections[:MAX_DETECTIONS] if isinstance(d, dict)]
    counts = answer.get("counts")
    if isinstance(counts, list):
        run.counts = [_count(c) for c in counts[: MAX_DETECTIONS * 8] if isinstance(c, dict)]
    totals = answer.get("totals")
    if isinstance(totals, dict):
        run.totals = {k: int(v) for k, v in totals.items() if isinstance(v, int) and not isinstance(v, bool)}
    run.checked_at = utcnow()
    return run.status != before


async def out(db: AsyncSession, run: ShikariRun, client: Shikari | None) -> ShikariRunOut:
    lookup = await views.names(db, [run.started_by])
    return ShikariRunOut(
        id=run.id,
        run_id=run.run_id,
        kind=run.kind,
        target=run.target,
        execute=run.execute,
        window_days=run.window_days,
        status=run.status,
        error=run.error,
        detections=run.detections or [],
        counts=[RunCount(**c) for c in run.counts or []],
        totals=run.totals or {},
        started_by=views.person(lookup, run.started_by),
        started_at=run.started_at,
        checked_at=run.checked_at,
        link=client.run_link(run.group_name) if client else None,
    )


@router.get("/runs", response_model=list[ShikariRunOut])
async def list_runs(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[ShikariRunOut]:
    incident = await access.load(db, user, incident_id)
    row = await configured(db, Product.SHIKARI)
    client = Shikari.of(row) if row else None
    runs = (
        await db.scalars(
            select(ShikariRun)
            .where(ShikariRun.incident_id == incident.id)
            .order_by(ShikariRun.started_at.desc())
        )
    ).all()
    return [await out(db, r, client) for r in runs]


@router.post("/runs", response_model=ShikariRunOut, status_code=status.HTTP_201_CREATED)
async def start_run(
    incident_id: uuid.UUID,
    body: ShikariRunIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ShikariRunOut:
    """Start a Shikari hunt run for this record. Shikari is told the target and the record's reference."""
    incident = await access.load(db, user, incident_id, edit=True)
    client = await server(db, incident)
    target = body.target.strip()
    if body.kind == "techniques":
        target = ",".join(t.strip().upper() for t in target.replace(" ", ",").split(",") if t.strip())
    try:
        answer = await client.start_run(
            body.kind, target, incident.ref, execute=body.execute, window_days=body.window_days
        )
    except Refused as exc:
        if exc.status == 403:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Shikari's token can't start hunts. Pair Ostraka with Shikari again (Settings → "
                "Integrations) to get one that can.",
            ) from exc
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT
            if exc.status in (400, 404, 422)
            else status.HTTP_502_BAD_GATEWAY,
            f"Shikari: {exc}",
        ) from exc
    except Unreachable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run_id = str(answer.get("id") or "") if isinstance(answer, dict) else ""
    if not run_id:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Shikari didn't say which run it started.")
    run = ShikariRun(
        id=uuid.uuid4(),
        incident_id=incident.id,
        run_id=run_id[:64],
        kind=body.kind,
        target=target[:500],
        execute=body.execute,
        window_days=body.window_days,
        status=str(answer.get("status") or "queued")[:16],
        group_name=str(answer.get("group_name") or "")[:200],
        error="",
        detections=[],
        counts=[],
        totals={},
        started_by=user.id,
        started_at=utcnow(),
    )
    db.add(run)
    then = f", then run its queries over the last {body.window_days} days" if body.execute else ""
    await activity.record(
        db,
        incident,
        user,
        "shikari_run_started",
        f"{user.name} started a Shikari hunt for {body.kind} {target}{then}",
        entity_type="shikari_run",
        entity_id=run.id,
    )
    await activity.finish(db, user)
    return await out(db, run, client)


@router.post("/runs/{run_id}/refresh", response_model=ShikariRunOut)
async def refresh_run(
    incident_id: uuid.UUID,
    run_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ShikariRunOut:
    incident = await access.load(db, user, incident_id)
    run = await db.get(ShikariRun, run_id)
    if run is None or run.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That Shikari run isn't in this record.")
    client = await server(db, incident)
    try:
        apply(run, await client.run(run.run_id))
    except (Unreachable, Refused) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    await db.commit()
    return await out(db, run, client)
