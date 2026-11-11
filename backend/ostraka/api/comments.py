"""Comment threads on an incident and on anything in it, with @mentions and #references."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import notify, work
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incident_schemas import TARGETS, CommentIn, CommentOut, CommentPatch, MaybeVersioned, Person
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import for_viewer
from ostraka.incidents.refs import mentions, plain
from ostraka.models import (
    Asset,
    Comment,
    Finding,
    Incident,
    IncidentObservable,
    Mention,
    NotificationKind,
    Role,
    Task,
    Team,
    User,
    utcnow,
)

router = APIRouter(prefix="/api/incidents/{incident_id}/comments", tags=["comments"])

_TARGET_MODELS = {"finding": Finding, "asset": Asset, "observable": IncidentObservable, "task": Task}


def _out(user: User, comment: Comment) -> CommentOut:
    deleted = comment.deleted_at is not None
    return CommentOut(
        id=comment.id,
        target_type=comment.target_type,
        target_id=comment.target_id,
        parent_id=comment.parent_id,
        author=Person(id=comment.author.id, name=comment.author.name) if comment.author else None,
        body="" if deleted else for_viewer(user, comment.body),
        created_at=comment.created_at,
        edited_at=comment.edited_at,
        deleted=deleted,
        resolved_at=comment.resolved_at,
        version=comment.version,
    )


async def _record_mentions(db: AsyncSession, comment: Comment) -> tuple[list[User], list[Team]]:
    user_ids, team_ids = mentions(comment.body)
    users = list((await db.scalars(select(User).where(User.id.in_(user_ids)))).all()) if user_ids else []
    teams = list((await db.scalars(select(Team).where(Team.id.in_(team_ids)))).all()) if team_ids else []
    db.add_all([Mention(comment_id=comment.id, user_id=u.id) for u in users])
    db.add_all([Mention(comment_id=comment.id, team_id=t.id) for t in teams])
    return users, teams


def _comment_link(incident: Incident, comment: Comment) -> str:
    sections = {"finding": "findings", "asset": "assets", "observable": "observables", "task": "tasks"}
    return notify.incident_link(incident, sections.get(comment.target_type, ""))


async def _notify_comment(
    db: AsyncSession, incident: Incident, comment: Comment, author: User, users: list[User], teams: list[Team]
) -> None:
    """Mentioned people and teams hear about it; so does the person who started the thread."""
    link = _comment_link(incident, comment)
    mentioned = list(users)
    for team in teams:
        mentioned += await work.team_members(db, team.id, observers=True)  # a team mention is for all of it
    notify.send(
        db,
        mentioned,
        NotificationKind.MENTION,
        f"{author.name} mentioned you in {incident.ref}",
        body=plain(comment.body),
        link=link,
        incident=incident,
        actor=author,
    )
    if comment.parent_id:
        root = await db.get(Comment, comment.parent_id)
        told = {u.id for u in mentioned}
        if root and root.author_id and root.author_id not in told:
            starter = await db.get(User, root.author_id)
            notify.send(
                db,
                [starter],
                NotificationKind.REPLY,
                f"{author.name} replied to you in {incident.ref}",
                body=plain(comment.body),
                link=link,
                incident=incident,
                actor=author,
            )


async def _comment(db: AsyncSession, incident: Incident, comment_id: uuid.UUID) -> Comment:
    comment = await db.get(Comment, comment_id)
    if comment is None or comment.incident_id != incident.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such comment in this incident.")
    return comment


@router.get("", response_model=list[CommentOut])
async def list_comments(
    incident_id: uuid.UUID,
    target_type: str | None = Query(default=None, pattern=f"^({'|'.join(TARGETS)})$"),
    target_id: uuid.UUID | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    """Comments in the order written. Without a target, every comment in the incident."""
    await access.load(db, user, incident_id)
    query = select(Comment).where(Comment.incident_id == incident_id).order_by(Comment.created_at)
    if target_type is not None:
        query = query.where(Comment.target_type == target_type)
        query = (
            query.where(Comment.target_id == target_id)
            if target_id
            else query.where(Comment.target_id.is_(None))
        )
    return [_out(user, c) for c in (await db.scalars(query)).all()]


@router.post("", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
async def add_comment(
    incident_id: uuid.UUID,
    body: CommentIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> CommentOut:
    incident = await access.load(db, user, incident_id, edit=True)
    target_id = None if body.target_type == "incident" else body.target_id
    if body.target_type != "incident":
        target = await db.get(_TARGET_MODELS[body.target_type], body.target_id) if body.target_id else None
        if target is None or target.incident_id != incident.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"That {body.target_type} isn't in this incident.")
    if body.parent_id is not None:
        parent = await _comment(db, incident, body.parent_id)
        if parent.parent_id is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Reply to the thread's first comment.")
        if (parent.target_type, parent.target_id) != (body.target_type, target_id):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "A reply belongs on the same item as its thread."
            )
    comment = Comment(
        incident_id=incident.id,
        target_type=body.target_type,
        target_id=target_id,
        parent_id=body.parent_id,
        author_id=user.id,
        body=body.body.strip(),
    )
    db.add(comment)
    await db.flush()
    users, teams = await _record_mentions(db, comment)
    await _notify_comment(db, incident, comment, user, users, teams)
    where = "" if body.target_type == "incident" else f" on a {body.target_type}"
    await activity.record(
        db,
        incident,
        user,
        "commented",
        f"{user.name} commented{where}",
        entity_type="comment",
        entity_id=comment.id,
        target_type=body.target_type,
        target_id=str(target_id) if target_id else None,
        mentions=len(users) + len(teams),
    )
    await activity.finish(db, user)
    await db.refresh(comment, ["author"])
    return _out(user, comment)


@router.patch("/{comment_id}", response_model=CommentOut)
async def edit_comment(
    incident_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: CommentPatch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> CommentOut:
    incident = await access.load(db, user, incident_id, edit=True)
    comment = await _comment(db, incident, comment_id)
    if comment.author_id != user.id or comment.deleted_at:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author can edit a comment.")
    views.check_version(comment.version, body.version, "comment")
    comment.body = body.body.strip()
    comment.edited_at = utcnow()
    comment.version += 1
    await activity.record(
        db,
        incident,
        user,
        "comment_edited",
        f"{user.name} edited a comment",
        entity_type="comment",
        entity_id=comment.id,
    )
    await activity.finish(db, user)
    return _out(user, comment)


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_comment(
    incident_id: uuid.UUID,
    comment_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Withdraw a comment. Its place in the thread stays, marked as withdrawn; the log keeps the event."""
    incident = await access.load(db, user, incident_id, edit=True)
    comment = await _comment(db, incident, comment_id)
    if comment.author_id != user.id and user.role is not Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author or an admin can withdraw a comment.")
    if comment.deleted_at is None:
        comment.deleted_at = utcnow()
        await activity.record(
            db,
            incident,
            user,
            "comment_withdrawn",
            f"{user.name} withdrew a comment",
            entity_type="comment",
            entity_id=comment.id,
        )
        await activity.finish(db, user)


@router.post("/{comment_id}/resolve", response_model=CommentOut)
async def resolve(
    incident_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: MaybeVersioned | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> CommentOut:
    """Mark a thread resolved, or reopen it if it already is. A toggle, so the version matters: two
    people resolving at once would otherwise reopen it."""
    incident = await access.load(db, user, incident_id, edit=True)
    comment = await _comment(db, incident, comment_id)
    if comment.parent_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Resolve the thread from its first comment."
        )
    if body is not None and body.version is not None:
        views.check_version(comment.version, body.version, "thread")
    reopening = comment.resolved_at is not None
    comment.version += 1
    comment.resolved_at = None if reopening else utcnow()
    comment.resolved_by = None if reopening else user.id
    await activity.record(
        db,
        incident,
        user,
        "thread_reopened" if reopening else "thread_resolved",
        f"{user.name} {'reopened' if reopening else 'resolved'} a thread",
        entity_type="comment",
        entity_id=comment.id,
    )
    await activity.finish(db, user)
    return _out(user, comment)
