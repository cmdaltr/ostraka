"""The catalogue: closed incidents and hunts searched on the server, and the lessons register.

Both follow each record's access (`access.visible_filter`), so a search never shows, or counts, a record
the viewer couldn't open. The catalogue matches a reference, code name or title (`q`), tags, a technique
on a finding that wasn't refuted (T1059 also finds T1059.001), and the dates a record was declared and
closed, a page at a time. Titles aren't masked anywhere, so nothing in it can match an indicator a
viewer sees masked. Lessons are: an observer whose view masks indicators matches only on what they see,
as on the Queries page, read in batches with no cap; their total is left out until it's all been read,
and `has_more` says whether there's a next page.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, and_, case, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import branding, siem
from ostraka.api.archive import record_ref
from ostraka.api.lessons import lesson_out
from ostraka.auth.deps import current_user
from ostraka.catalogue_schemas import CatalogueEntry, CataloguePage, LessonPage, RegisterLesson
from ostraka.db import get_session
from ostraka.incidents import access, views
from ostraka.incidents.masking import for_viewer, hides_iocs
from ostraka.incidents.refs import parse_ref
from ostraka.models import (
    FINISHED,
    ArchiveState,
    Finding,
    FindingStatus,
    Incident,
    IncidentStatus,
    LessonArea,
    LessonKind,
    RecordKind,
    Task,
    User,
)
from ostraka.models import Lesson as LessonRow

router = APIRouter(prefix="/api/catalogue", tags=["catalogue"])

TECHNIQUE = re.compile(r"^T\d{4}(\.\d{3})?$")
MAX_PER_PAGE = 100
MASKED_BATCH = 500  # lessons read at a time to match an observer's masked view


def _ref_text():
    """A record's reference as SQL writes it (OST-26-014), to match part of one."""
    prefix = case((Incident.kind == RecordKind.HUNT, branding.HUNT_PREFIX), else_=branding.INCIDENT_PREFIX)
    year = func.lpad(cast(Incident.year % 100, String), 2, "0")
    number = func.lpad(cast(Incident.number, String), 3, "0")
    return func.concat(prefix, "-", year, "-", number)


def _start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _technique(text: str) -> str:
    technique = text.strip().upper()
    if not TECHNIQUE.match(technique):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"“{text}” isn't an ATT&CK technique (T1059, T1059.001)."
        )
    return technique


def _between(column, after: date | None, before: date | None) -> list:
    """`after` to `before`, both days included."""
    clauses = []
    if after is not None:
        clauses.append(column >= _start(after))
    if before is not None:
        clauses.append(column < _start(before) + timedelta(days=1))
    return clauses


def _page(page: int, per_page: int) -> tuple[int, int]:
    return max(page, 1), min(max(per_page, 1), MAX_PER_PAGE)


@router.get("", response_model=CataloguePage)
async def search(
    q: str = Query(default="", max_length=200, description="Part of a reference, code name or title"),
    kind: RecordKind | None = None,
    state: list[ArchiveState] = Query(default=[], description="Closed, archived or cold; any if none"),
    tag: list[str] = Query(default=[], description="Every one of these tags"),
    technique: str = Query(default="", max_length=16, description="On a finding that wasn't refuted"),
    opened_from: date | None = None,
    opened_to: date | None = None,
    closed_from: date | None = None,
    closed_to: date | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=MAX_PER_PAGE),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> CataloguePage:
    """Closed incidents and hunts the viewer can see, most recently closed first."""
    page, per_page = _page(page, per_page)
    where = [Incident.status == IncidentStatus.CLOSED, access.visible_filter(user)]
    term = q.strip()
    if term:
        pattern = siem.like(term)
        matches = [
            Incident.title.ilike(pattern, escape="\\"),
            Incident.codename.ilike(pattern, escape="\\"),
            _ref_text().ilike(pattern, escape="\\"),
        ]
        ref = parse_ref(term)
        if ref is not None and ref.kind == "incident":  # the older four-digit year, OST-2026-014, too
            record = RecordKind.HUNT if ref.record == "hunt" else RecordKind.INCIDENT
            matches.append(
                and_(Incident.kind == record, Incident.year == ref.year, Incident.number == ref.number)
            )
        where.append(or_(*matches))
    if kind is not None:
        where.append(Incident.kind == kind)
    if state:
        where.append(Incident.archive_state.in_(state))
    for wanted in tag:
        if cleaned := wanted.strip().lower().replace(" ", "-"):
            where.append(Incident.tags.any(cleaned))
    if technique.strip():
        wanted = _technique(technique)
        # whole IDs only: T1059 matches T1059 and T1059.001, never T10591
        listed = func.concat(" ", func.array_to_string(Finding.techniques, " "), " ")
        where.append(
            exists().where(
                Finding.incident_id == Incident.id,
                Finding.status != FindingStatus.REFUTED,
                or_(listed.like(f"% {wanted} %"), listed.like(f"% {wanted}.%")),
            )
        )
    where += _between(Incident.declared_at, opened_from, opened_to)
    where += _between(Incident.closed_at, closed_from, closed_to)

    total = await db.scalar(select(func.count()).select_from(Incident).where(*where)) or 0
    rows = (
        (
            await db.scalars(
                select(Incident)
                .where(*where)
                .order_by(Incident.closed_at.desc().nulls_last(), Incident.id)
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        )
        .unique()
        .all()
    )
    techniques: dict = defaultdict(set)
    if rows:
        found = await db.execute(
            select(Finding.incident_id, func.unnest(Finding.techniques)).where(
                Finding.incident_id.in_([r.id for r in rows]), Finding.status != FindingStatus.REFUTED
            )
        )
        for incident_id, found_technique in found.all():
            techniques[incident_id].add(found_technique)
    items = [
        CatalogueEntry(
            **views.summary(r).model_dump(),
            closed_at=r.closed_at,
            archived_at=r.archived_at,
            cold_at=r.cold_at,
            techniques=sorted(techniques[r.id]),
        )
        for r in rows
    ]
    return CataloguePage(
        items=items, total=total, has_more=page * per_page < total, page=page, per_page=per_page
    )


@router.get("/lessons", response_model=LessonPage)
async def lessons(
    q: str = Query(default="", max_length=200, description="Part of the observation or recommendation"),
    area: list[LessonArea] = Query(default=[]),
    kind: LessonKind | None = None,
    follow_up: str = Query(default="", pattern="^(|none|open|done)$"),
    record_kind: RecordKind | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=MAX_PER_PAGE),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> LessonPage:
    """Every lesson in the records the viewer can see, open or closed, newest first, with its record."""
    page, per_page = _page(page, per_page)
    where = [access.visible_filter(user)]
    term = q.strip()
    if term:
        pattern = siem.like(term)
        where.append(
            or_(
                LessonRow.observation.ilike(pattern, escape="\\"),
                LessonRow.recommendation.ilike(pattern, escape="\\"),
            )
        )
    if area:
        where.append(LessonRow.area.in_(area))
    if kind is not None:
        where.append(LessonRow.kind == kind)
    if record_kind is not None:
        where.append(Incident.kind == record_kind)
    if follow_up == "none":
        where.append(LessonRow.task_id.is_(None))
    elif follow_up in ("open", "done"):
        finished = Task.status.in_(FINISHED)
        where.append(
            exists().where(Task.id == LessonRow.task_id, finished if follow_up == "done" else ~finished)
        )
    base = select(LessonRow, Incident).join(Incident, Incident.id == LessonRow.incident_id).where(*where)
    ordered = base.order_by(LessonRow.created_at.desc(), LessonRow.seq.desc())

    async def entry(row: LessonRow, incident: Incident) -> RegisterLesson:
        shown = await lesson_out(db, user, row)
        return RegisterLesson(**shown.model_dump(), record=record_ref(incident))

    if term and hides_iocs(user):
        # Match on what this viewer sees: an indicator they see masked mustn't find the lesson. Read in
        # batches until this page and one more match are found, so there's no cap on how far it looks;
        # the total is only known once every candidate has been read.
        wanted = page * per_page + 1
        matched: list[tuple[LessonRow, Incident]] = []
        offset, exhausted = 0, False
        while len(matched) < wanted:
            batch = (await db.execute(ordered.offset(offset).limit(MASKED_BATCH))).tuples().all()
            for row, incident in batch:
                if term.lower() in for_viewer(user, f"{row.observation}\n{row.recommendation}").lower():
                    matched.append((row, incident))
            offset += len(batch)
            if len(batch) < MASKED_BATCH:
                exhausted = True
                break
        start = (page - 1) * per_page
        shown = [await entry(row, incident) for row, incident in matched[start : start + per_page]]
        more = len(matched) > start + per_page
        return LessonPage(
            items=shown,
            total=len(matched) if exhausted else None,
            has_more=more,
            page=page,
            per_page=per_page,
        )

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (await db.execute(ordered.offset((page - 1) * per_page).limit(per_page))).tuples().all()
    return LessonPage(
        items=[await entry(row, incident) for row, incident in rows],
        total=total,
        has_more=page * per_page < total,
        page=page,
        per_page=per_page,
    )
