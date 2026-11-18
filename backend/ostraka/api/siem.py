"""SIEM queries (`ostraka/siem.py`): the connections an admin adds, running a query through one, and
searching every query run. Only the query is stored; its rows are in the response to the person who ran
it and nowhere else."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, model_validator
from samarkand.siem import SiemProblem
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit, busy, siem
from ostraka.auth import policy, sessions
from ostraka.auth.deps import current_user
from ostraka.auth.secrets import encrypt
from ostraka.db import get_session
from ostraka.incident_schemas import Person
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer
from ostraka.log import get_logger
from ostraka.models import Incident, QueryOutcome, SiemConnection, SiemKind, SiemQuery, User, utcnow

log = get_logger(__name__)
router = APIRouter(prefix="/api/siem", tags=["siem"])

_running = 0  # queries in flight: a restart would cut them off
busy.register("siem-queries", lambda: _running > 0)


class FieldOut(BaseModel):
    key: str
    label: str
    secret: bool
    required: bool
    hint: str


class KindOut(BaseModel):
    kind: SiemKind
    name: str
    language: str
    window: str
    fields: list[FieldOut]


class ConnectionOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: SiemKind
    language: str
    enabled: bool
    # for admins only: what isn't secret, which secrets are set, and the last check
    settings: dict[str, str] | None = None
    secrets_set: list[str] | None = None
    verify_tls: bool | None = None
    checked_at: datetime | None = None
    check_ok: bool | None = None
    check_message: str = ""
    version: int | None = None


class ConnectionIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    kind: SiemKind
    values: dict[str, str] = Field(default_factory=dict)
    verify_tls: bool = True
    enabled: bool = True


class ConnectionPatch(BaseModel):
    """A secret left out or sent empty keeps the one stored."""

    version: int
    name: str | None = Field(default=None, min_length=2, max_length=80)
    values: dict[str, str] | None = None
    verify_tls: bool | None = None
    enabled: bool | None = None


class RunIn(BaseModel):
    connection_id: uuid.UUID
    text: str = Field(min_length=1, max_length=50_000)
    incident_id: uuid.UUID | None = None
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def _window(self) -> RunIn:
        if self.start and self.end and self.start >= self.end:
            raise ValueError("The window must start before it ends.")
        return self


class IncidentRef(BaseModel):
    id: uuid.UUID
    ref: str
    codename: str | None


class QueryOut(BaseModel):
    id: uuid.UUID
    ref: str
    text: str
    kind: SiemKind
    language: str
    connection_id: uuid.UUID | None
    connection_name: str
    incident: IncidentRef | None
    window_start: datetime | None
    window_end: datetime | None
    outcome: QueryOutcome
    row_count: int | None
    truncated: bool
    run_by: Person | None
    run_at: datetime


class RunOut(BaseModel):
    query: QueryOut
    columns: list[str]
    rows: list[dict]
    truncated: bool
    problem: str = ""


def _admin(user: User) -> None:
    if reason := policy.can_manage_siems(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)


def _connection_out(row: SiemConnection, user: User) -> ConnectionOut:
    out = ConnectionOut(
        id=row.id, name=row.name, kind=row.kind, language=siem.KINDS[row.kind].language, enabled=row.enabled
    )
    if policy.can_manage_siems(user) is None:
        out.settings = dict(row.settings or {})
        out.secrets_set = sorted(siem.secrets_of(row))
        out.verify_tls, out.version = row.verify_tls, row.version
        out.checked_at, out.check_ok, out.check_message = row.checked_at, row.check_ok, row.check_message
    return out


async def _query_out(db: AsyncSession, user: User, row: SiemQuery, incident: Incident | None) -> QueryOut:
    lookup = await views.names(db, [row.run_by])
    return QueryOut(
        id=row.id,
        ref=row.ref,
        text=for_viewer(user, row.text),
        kind=row.kind,
        language=siem.KINDS[row.kind].language,
        connection_id=row.connection_id,
        connection_name=row.connection_name,
        incident=(
            IncidentRef(id=incident.id, ref=incident.ref, codename=incident.codename) if incident else None
        ),
        window_start=row.window_start,
        window_end=row.window_end,
        outcome=row.outcome,
        row_count=row.row_count,
        truncated=row.truncated,
        run_by=views.person(lookup, row.run_by),
        run_at=row.run_at,
    )


async def _named(db: AsyncSession, name: str, other: uuid.UUID | None = None) -> None:
    taken = await db.scalar(select(SiemConnection.id).where(SiemConnection.name == name.strip()))
    if taken is not None and taken != other:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A connection is already called {name.strip()}.")


# ---- connections --------------------------------------------------------------------------------------


@router.get("/kinds", response_model=list[KindOut])
async def list_kinds(user: User = Depends(current_user)) -> list[KindOut]:
    """The SIEMs Ostraka can query, and what connecting each takes."""
    return [
        KindOut(
            kind=k,
            name=v.name,
            language=v.language,
            window=v.window,
            fields=[FieldOut(**f.__dict__) for f in v.fields],
        )
        for k, v in siem.KINDS.items()
    ]


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[ConnectionOut]:
    """The connected SIEMs: every one for an admin, with its settings; the enabled ones for anyone else."""
    rows = (await db.scalars(select(SiemConnection).order_by(SiemConnection.name))).all()
    admin = policy.can_manage_siems(user) is None
    return [_connection_out(r, user) for r in rows if admin or r.enabled]


@router.post("/connections", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED)
async def add_connection(
    body: ConnectionIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ConnectionOut:
    """Connect a SIEM. Its secrets are encrypted and never shown again."""
    _admin(user)
    await _named(db, body.name)
    settings, secrets = siem.split(body.kind, body.values)
    if reason := siem.problem(body.kind, settings, secrets):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, reason)
    row = SiemConnection(
        name=body.name.strip(),
        kind=body.kind,
        settings=settings,
        secret=encrypt(json.dumps(secrets)) if secrets else "",
        verify_tls=body.verify_tls,
        enabled=body.enabled,
        updated_by=user.id,
        updated_at=utcnow(),
        version=1,
        check_message="",
    )
    db.add(row)
    await db.flush()
    audit.record(
        db,
        "siem_connected",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        connection=row.name,
        kind=row.kind.value,
        verify_tls=row.verify_tls,
    )
    await db.commit()
    return _connection_out(row, user)


@router.patch("/connections/{connection_id}", response_model=ConnectionOut)
async def change_connection(
    connection_id: uuid.UUID,
    body: ConnectionPatch,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ConnectionOut:
    """Change a connection. A secret left empty keeps the one stored."""
    _admin(user)
    row = await db.get(SiemConnection, connection_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such connection.")
    views.check_version(row.version, body.version, "connection")
    if body.name is not None:
        await _named(db, body.name, row.id)
        row.name = body.name.strip()
    secrets_changed: list[str] = []
    if body.values is not None:
        settings, sent = siem.split(row.kind, body.values)
        secrets = siem.secrets_of(row) | sent
        if reason := siem.problem(row.kind, settings, secrets):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, reason)
        secrets_changed = sorted(sent)
        row.settings, row.secret = settings, encrypt(json.dumps(secrets)) if secrets else ""
        row.checked_at, row.check_ok, row.check_message = None, None, ""
    if body.verify_tls is not None:
        row.verify_tls = body.verify_tls
    if body.enabled is not None:
        row.enabled = body.enabled
    row.version += 1
    row.updated_by, row.updated_at = user.id, utcnow()
    audit.record(
        db,
        "siem_changed",
        actor_id=user.id,
        ip=sessions.client_ip(request),
        connection=row.name,
        secrets_changed=secrets_changed,
        verify_tls=row.verify_tls,
        enabled=row.enabled,
    )
    await db.commit()
    return _connection_out(row, user)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_connection(
    connection_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Remove a connection and its secrets. The queries run through it stay, under its name."""
    _admin(user)
    row = await db.get(SiemConnection, connection_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such connection.")
    audit.record(db, "siem_removed", actor_id=user.id, ip=sessions.client_ip(request), connection=row.name)
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/connections/{connection_id}/test", response_model=ConnectionOut)
async def test_connection(
    connection_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> ConnectionOut:
    """Run the SIEM's trivial query with the stored credentials, and keep the answer."""
    _admin(user)
    row = await db.get(SiemConnection, connection_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such connection.")
    try:
        await siem.client_for(row).check_async()
        row.check_ok, row.check_message = True, "Connected, and a query ran."
    except (SiemProblem, ValueError) as exc:
        row.check_ok, row.check_message = False, str(exc)[:300]
    row.checked_at = utcnow()
    await db.commit()
    return _connection_out(row, user)


# ---- queries ------------------------------------------------------------------------------------------


@router.post("/queries", response_model=RunOut)
async def run_query(
    body: RunIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> RunOut:
    """Run a query and keep it (never its rows). A query the SIEM refuses is kept too, as failed, with
    the SIEM's reason in the answer only."""
    global _running
    if reason := policy.can_run_queries(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)
    incident = await access.load(db, user, body.incident_id, edit=True) if body.incident_id else None
    connection = await db.get(SiemConnection, body.connection_id)
    if connection is None or not connection.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such connection.")
    row = SiemQuery(
        text=body.text.strip(),
        kind=connection.kind,
        connection_id=connection.id,
        connection_name=connection.name,
        incident_id=incident.id if incident else None,
        window_start=body.start,
        window_end=body.end,
        run_by=user.id,
        run_at=utcnow(),
        outcome=QueryOutcome.FAILED,
        truncated=False,
    )
    rows: list[dict] = []
    columns: list[str] = []
    problem = ""
    _running += 1
    try:
        result = await siem.client_for(connection).query_async(
            row.text, start=body.start, end=body.end, max_rows=siem.MAX_ROWS, timeout=siem.TIMEOUT
        )
        rows, columns = result.rows, result.columns
        row.outcome, row.row_count, row.truncated = QueryOutcome.OK, len(rows), result.truncated
    except (SiemProblem, ValueError) as exc:
        problem = str(exc)[:1000] or "The query failed."
    finally:
        _running -= 1
    db.add(row)
    await db.flush()
    await db.refresh(row, ["seq"])
    if incident is not None:
        said = "it failed"
        if row.outcome is QueryOutcome.OK:
            said = f"{len(rows)} row{'s' if len(rows) != 1 else ''}"
        await activity.record(
            db,
            incident,
            user,
            "query_run",
            f"{user.name} ran {row.ref} on {connection.name}: {said}",
            entity_type="query",
            entity_id=row.id,
        )
        await activity.finish(db, user)
    else:
        await db.commit()
    return RunOut(
        query=await _query_out(db, user, row, incident),
        columns=columns,
        rows=rows,
        truncated=row.truncated,
        problem=problem,
    )


@router.get("/queries", response_model=list[QueryOut])
async def search_queries(
    q: str = Query(default="", max_length=500, description="Any part of the query's text"),
    incident_id: uuid.UUID | None = None,
    kind: SiemKind | None = None,
    mine: bool = False,
    before: int | None = Query(default=None, description="Only queries older than this QRY number"),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[QueryOut]:
    """Queries run, newest first: those for records the viewer can see, and those run for no record.
    Observers see indicators masked, and match on what they see."""
    visible = select(Incident.id).where(access.visible_filter(user))
    stmt = (
        select(SiemQuery, Incident)
        .outerjoin(Incident, Incident.id == SiemQuery.incident_id)
        .where(or_(SiemQuery.incident_id.is_(None), SiemQuery.incident_id.in_(visible)))
        .order_by(SiemQuery.seq.desc())
    )
    term = q.strip()
    if term:
        stmt = stmt.where(SiemQuery.text.ilike(siem.like(term), escape="\\"))
    if incident_id is not None:
        stmt = stmt.where(SiemQuery.incident_id == incident_id)
    if kind is not None:
        stmt = stmt.where(SiemQuery.kind == kind)
    if mine:
        stmt = stmt.where(SiemQuery.run_by == user.id)
    if before is not None:
        stmt = stmt.where(SiemQuery.seq < before)
    out: list[QueryOut] = []
    for row, incident in (await db.execute(stmt.limit(limit * 2 if term else limit))).tuples():
        shown = await _query_out(db, user, row, incident)
        if term and term.lower() not in shown.text.lower():
            continue  # matched an indicator this viewer sees masked
        out.append(shown)
        if len(out) == limit:
            break
    return out
