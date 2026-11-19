"""Pnyx: an incident's bearings, and free questions about it (`ostraka/pnyx.py`)."""

from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import pnyx
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incidents import access
from ostraka.incidents.masking import for_viewer
from ostraka.log import get_logger
from ostraka.models import User, utcnow
from ostraka.updates import ai

log = get_logger(__name__)
router = APIRouter(prefix="/api/incidents/{incident_id}/pnyx", tags=["pnyx"])


class AnswerOut(BaseModel):
    key: str
    question: str
    lines: list[str]


class BearingsOut(BaseModel):
    answers: list[AnswerOut]
    can_ask: bool  # a model may read this incident
    why_not: str = ""  # why it can't, in words


class QuestionIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class ReplyOut(BaseModel):
    answer: str
    provider: str


@router.get("", response_model=BearingsOut)
async def bearings(
    incident_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)
) -> BearingsOut:
    """The standing questions, answered by Ostraka's own rules from the record, masked for the asker."""
    incident = await access.load(db, user, incident_id)
    facts = await pnyx.gather(db, incident, user, utcnow())
    try:
        ai.provider_for(incident)
        why = ""
    except ai.Unavailable as e:
        why = str(e)
    return BearingsOut(
        answers=[
            AnswerOut(key=a.key, question=a.question, lines=[for_viewer(user, line) for line in a.lines])
            for a in pnyx.bearings(facts)
        ],
        can_ask=not why,
        why_not=why,
    )


@router.post("/ask", response_model=ReplyOut)
async def ask(
    incident_id: uuid.UUID,
    body: QuestionIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> ReplyOut:
    """A free question, answered by a model from the masked record. An answer that cites what the record
    doesn't hold, calls a lead confirmed or asserts certainty is set aside rather than shown."""
    incident = await access.load(db, user, incident_id)
    try:
        provider = ai.provider_for(incident)
    except ai.Unavailable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    facts = await pnyx.gather(db, incident, user, utcnow())
    log.info("pnyx question", extra=pnyx.asked(incident.id, user, provider))
    try:
        answer = (await ai.complete(provider, pnyx.prompt(facts, body.question))).strip()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("pnyx with %s failed: %s", provider, e)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The model didn’t answer. Try again shortly."
        ) from None
    problems = pnyx.problems(facts, answer)
    if problems:
        log.info("pnyx answer set aside: %s", "; ".join(problems))
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Pnyx's answer was set aside: " + " ".join(problems) + " Try asking another way.",
        )
    return ReplyOut(answer=for_viewer(user, answer), provider=provider)
