"""Handover and presence: "since you left", shift-handover notes, and work on the same indicator in
other incidents."""

from __future__ import annotations

import uuid
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import handover
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.handover_schemas import (
    ElsewhereWorkOut,
    HandoverDraft,
    HandoverIn,
    HandoverOut,
    HandoverSection,
    SectionChange,
    SeenIn,
    SinceOut,
    WorkingOut,
)
from ostraka.incident_schemas import ActivityOut, Person
from ostraka.incidents import access, activity
from ostraka.incidents.masking import for_viewer, hides_iocs
from ostraka.incidents.presence import working_elsewhere
from ostraka.models import ActivityEvent, HandoverNote, IncidentView, User, utcnow

router = APIRouter(prefix="/api/incidents/{incident_id}", tags=["handover"])

SINCE_EVENTS = 100  # listed in "since you left"; the counts cover them all


def _activity(user: User, event: ActivityEvent) -> ActivityOut:
    return ActivityOut(
        position=event.position,
        at=event.at,
        actor=Person(id=event.actor.id, name=event.actor.name) if event.actor else None,
        action=event.action,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        summary=for_viewer(user, event.summary),
        data={} if hides_iocs(user) else event.data,
    )


@router.get("/since", response_model=SinceOut)
async def since_you_left(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> SinceOut:
    """What other people changed after the last position you saw. Reading it doesn't move your place:
    the browser says what it showed with POST /seen."""
    incident = await access.load(db, user, incident_id)
    view = await db.get(IncidentView, (incident.id, user.id))
    last = await handover.through(db, incident.id)
    if view is None:
        return SinceOut(
            first_visit=True,
            last_viewed_at=None,
            from_position=last,
            through_position=last,
            total=0,
            changes=[],
            people=[],
            events=[],
        )
    events = (
        await db.scalars(
            select(ActivityEvent)
            .where(
                ActivityEvent.incident_id == incident.id,
                ActivityEvent.position > view.last_position,
                ActivityEvent.position <= last,
                # Your own changes aren't news to you
                (ActivityEvent.actor_id.is_(None)) | (ActivityEvent.actor_id != user.id),
            )
            .order_by(ActivityEvent.position)
        )
    ).all()
    kinds = Counter(e.entity_type for e in events)
    return SinceOut(
        first_visit=False,
        last_viewed_at=view.viewed_at,
        from_position=view.last_position,
        through_position=last,
        total=len(events),
        changes=[
            SectionChange(entity_type=k, count=n) for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])
        ],
        people=sorted({e.actor.name for e in events if e.actor}),
        events=[_activity(user, e) for e in events[-SINCE_EVENTS:]],
    )


@router.post("/seen", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def seen(
    incident_id: uuid.UUID,
    body: SeenIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Record how far through the log you've seen. Your place only ever moves forward."""
    incident = await access.load(db, user, incident_id)
    last = await handover.through(db, incident.id)
    position = last if body.position is None else min(body.position, last)
    statement = insert(IncidentView).values(
        incident_id=incident.id, user_id=user.id, last_position=position, viewed_at=utcnow()
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[IncidentView.incident_id, IncidentView.user_id],
            set_={
                "last_position": func.greatest(IncidentView.last_position, statement.excluded.last_position),
                "viewed_at": statement.excluded.viewed_at,
            },
        )
    )
    await db.commit()


def _note_out(user: User, note: HandoverNote) -> HandoverOut:
    return HandoverOut(
        id=note.id,
        ref=f"HND-{note.seq}",
        author=Person(id=note.author.id, name=note.author.name) if note.author else None,
        from_position=note.from_position,
        through_position=note.through_position,
        body=for_viewer(user, note.body),
        edited=note.body.strip() != note.generated.strip(),
        created_at=note.created_at,
    )


@router.get("/handover", response_model=list[HandoverOut])
async def list_handovers(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[HandoverOut]:
    """Posted handover notes, newest first, with indicators masked for the viewer."""
    incident = await access.load(db, user, incident_id)
    notes = (
        await db.scalars(
            select(HandoverNote)
            .where(HandoverNote.incident_id == incident.id)
            .order_by(HandoverNote.created_at.desc(), HandoverNote.seq.desc())
        )
    ).all()
    return [_note_out(user, n) for n in notes]


@router.get("/handover/draft", response_model=HandoverDraft)
async def handover_draft(
    incident_id: uuid.UUID,
    after: int | None = Query(default=None, ge=0, description="Cover the log after this position"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> HandoverDraft:
    """The note Ostraka writes from the log and the incident's state, for the person handing over to
    edit. By default it covers everything since the last handover posted on the incident."""
    incident = await access.load(db, user, incident_id, edit=True)
    state = await handover.gather(db, incident, user, after)
    made = handover.draft(state)
    return HandoverDraft(
        from_position=state.from_position,
        through_position=state.through_position,
        since=state.since,
        sections=[HandoverSection(title=s.title, lines=s.lines) for s in made.sections],
        text=made.text,
    )


@router.post("/handover", response_model=HandoverOut, status_code=status.HTTP_201_CREATED)
async def post_handover(
    incident_id: uuid.UUID,
    body: HandoverIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> HandoverOut:
    """Post the confirmed note to the incident and its activity log. The draft is regenerated for the
    same stretch of the log, so what Ostraka wrote is kept beside what was posted."""
    incident = await access.load(db, user, incident_id, edit=True)
    last = await handover.through(db, incident.id)
    if not body.from_position <= body.through_position <= last:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "That stretch of the activity log doesn't exist here."
        )
    state = await handover.gather(db, incident, user, body.from_position)
    state.through_position = body.through_position
    state.events = state.events[: max(0, body.through_position - body.from_position)]
    note = HandoverNote(
        incident_id=incident.id,
        author_id=user.id,
        from_position=body.from_position,
        through_position=body.through_position,
        generated=handover.draft(state).text,
        body=body.body.strip(),
    )
    db.add(note)
    await db.flush()
    await db.refresh(note, ["seq"])
    await activity.record(
        db,
        incident,
        user,
        "handover_posted",
        f"{user.name} posted handover HND-{note.seq}",
        entity_type="handover",
        entity_id=note.id,
        covers=[body.from_position, body.through_position],
    )
    await activity.finish(db, user)
    await db.refresh(note, ["author"])
    return _note_out(user, note)


@router.get("/observables/working", response_model=list[WorkingOut])
async def working(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> list[WorkingOut]:
    """Observables here that someone else is working in another open incident right now: open on
    their screen, or changed in the last two hours. Incidents you can't see are only counted."""
    incident = await access.load(db, user, incident_id)
    return [
        WorkingOut(
            item_id=w.item_id,
            visible=[
                ElsewhereWorkOut(
                    incident_id=v.incident_id,
                    ref=v.ref,
                    title=for_viewer(user, v.title),
                    people=sorted(v.people),
                    editing=v.editing,
                    last_at=v.last_at,
                )
                for v in w.visible
            ],
            hidden=w.hidden,
            ask=w.ask,
        )
        for w in await working_elsewhere(db, user, incident)
    ]
