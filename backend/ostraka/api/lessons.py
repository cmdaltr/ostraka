"""Lessons learned: what went well and what we'd change, each improvement able to become a task."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka.analysis_schemas import FollowUpIn, LessonIn, LessonOut, LessonPatch, TaskRef
from ostraka.api.tasks import create_task
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer
from ostraka.models import Incident, Lesson, LessonKind, Task, User
from ostraka.work_schemas import TaskIn

router = APIRouter(prefix="/api/incidents/{incident_id}/lessons", tags=["lessons"])

KIND_WORDS = {LessonKind.WENT_WELL: "went well", LessonKind.IMPROVE: "to improve"}


async def lesson_out(db: AsyncSession, user: User, lesson: Lesson) -> LessonOut:
    lookup = await views.names(db, [lesson.created_by])
    task = await db.get(Task, lesson.task_id) if lesson.task_id else None
    return LessonOut(
        id=lesson.id,
        ref=f"LSN-{lesson.seq}",
        kind=lesson.kind,
        area=lesson.area,
        observation=for_viewer(user, lesson.observation),
        recommendation=for_viewer(user, lesson.recommendation),
        task=TaskRef(id=task.id, ref=task.ref, status=task.status) if task else None,
        created_by=views.person(lookup, lesson.created_by),
        created_at=lesson.created_at,
        version=lesson.version,
    )


async def _lesson(db: AsyncSession, incident: Incident, lesson_id: uuid.UUID) -> Lesson:
    lesson = await db.get(Lesson, lesson_id)
    if lesson is None or lesson.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That lesson isn't in this incident.")
    return lesson


@router.get("", response_model=list[LessonOut])
async def list_lessons(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[LessonOut]:
    await access.load(db, user, incident_id)
    rows = await db.scalars(select(Lesson).where(Lesson.incident_id == incident_id).order_by(Lesson.seq))
    return [await lesson_out(db, user, lesson) for lesson in rows.all()]


@router.post("", response_model=LessonOut, status_code=status.HTTP_201_CREATED)
async def add_lesson(
    incident_id: uuid.UUID,
    body: LessonIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> LessonOut:
    incident = await access.load(db, user, incident_id, edit=True)
    lesson = Lesson(
        incident_id=incident.id,
        kind=body.kind,
        area=body.area,
        observation=body.observation.strip(),
        recommendation=body.recommendation.strip(),
        created_by=user.id,
    )
    db.add(lesson)
    await db.flush()
    await activity.record(
        db,
        incident,
        user,
        "lesson_added",
        f"{user.name} recorded LSN-{lesson.seq} ({body.area.value}, {KIND_WORDS[body.kind]}): "
        f"{lesson.observation}",
        entity_type="lesson",
        entity_id=lesson.id,
    )
    await activity.finish(db, user)
    return await lesson_out(db, user, lesson)


@router.patch("/{lesson_id}", response_model=LessonOut)
async def update_lesson(
    incident_id: uuid.UUID,
    lesson_id: uuid.UUID,
    body: LessonPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> LessonOut:
    incident = await access.load(db, user, incident_id, edit=True)
    lesson = await _lesson(db, incident, lesson_id)
    views.check_version(lesson.version, body.version, "lesson")
    changes = {
        k: v for k, v in body.model_dump(exclude_unset=True, exclude={"version"}).items() if v is not None
    }
    for field, value in changes.items():
        setattr(lesson, field, value.strip() if isinstance(value, str) else value)
    if changes:
        lesson.version += 1
        await activity.record(
            db,
            incident,
            user,
            "lesson_updated",
            f"{user.name} edited LSN-{lesson.seq}",
            entity_type="lesson",
            entity_id=lesson.id,
            fields=sorted(changes),
        )
        await activity.finish(db, user)
    return await lesson_out(db, user, lesson)


@router.delete("/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def remove_lesson(
    incident_id: uuid.UUID,
    lesson_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    incident = await access.load(db, user, incident_id, edit=True)
    lesson = await _lesson(db, incident, lesson_id)
    await activity.record(
        db,
        incident,
        user,
        "lesson_removed",
        f"{user.name} removed LSN-{lesson.seq}: {lesson.observation}",
        entity_type="lesson",
        entity_id=lesson.id,
    )
    await db.delete(lesson)
    await activity.finish(db, user)


@router.post("/{lesson_id}/task", response_model=LessonOut)
async def follow_up(
    incident_id: uuid.UUID,
    lesson_id: uuid.UUID,
    body: FollowUpIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> LessonOut:
    """Make an improvement a task, so it's owned and chased like any other work."""
    incident = await access.load(db, user, incident_id, edit=True)
    lesson = await _lesson(db, incident, lesson_id)
    if lesson.task_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "That lesson already has a follow-up task.")
    task = await create_task(
        db,
        incident,
        user,
        TaskIn(
            title=body.title or (lesson.recommendation or lesson.observation)[:200],
            description=f"From LSN-{lesson.seq} ({lesson.area.value}): {lesson.observation}",
            team_id=body.team_id,
            assignee_id=body.assignee_id,
            due_at=body.due_at,
        ),
        origin=f"from LSN-{lesson.seq}",
    )
    lesson.task_id = task.id
    lesson.version += 1
    await activity.finish(db, user)
    return await lesson_out(db, user, lesson)
