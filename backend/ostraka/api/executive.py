"""A record's executive summary (`ostraka/executive.py`): drafting it, by Ostraka's rules or a checked
model, and saving it."""

from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import executive
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incident_schemas import IncidentOut
from ostraka.incidents import access, activity, views
from ostraka.incidents.masking import mask_text
from ostraka.log import get_logger
from ostraka.models import User, utcnow
from ostraka.updates import ai

log = get_logger(__name__)
router = APIRouter(prefix="/api/incidents/{incident_id}/executive-summary", tags=["executive summary"])

WORDED_BY = {"ostraka": "Ostraka", "ollama": "Ollama", "anthropic": "Anthropic", "person": "hand"}


class DraftIn(BaseModel):
    ai: bool = Field(default=True, description="Let a model word it, when the TLP guard allows one")


class DraftOut(BaseModel):
    sentences: list[str]
    text: str
    worded: str  # ostraka, ollama or anthropic
    set_aside: str = ""  # why a model's wording wasn't used
    can_ask: bool  # a model may read this record
    why_not: str = ""


class SummaryIn(BaseModel):
    version: int
    text: str = Field(max_length=executive.SAVED_MAX)
    worded: str = "person"


async def _model(provider: str, facts: executive.Facts) -> tuple[list[str] | None, str]:
    try:
        reply = await ai.complete(provider, executive.prompt(facts))
    except (httpx.HTTPError, ValueError) as e:
        log.warning("executive summary with %s failed: %s", provider, e)
        return None, "The model didn’t answer."
    sentences = executive.parse(reply)
    if sentences is None:
        return None, "The model didn’t reply with four sentences."
    sentences = [mask_text(s) for s in sentences]
    problems = executive.problems(facts, sentences)
    if problems:
        log.info("executive summary set aside: %s", "; ".join(problems))
        return None, " ".join(problems)
    return sentences, ""


@router.post("/draft", response_model=DraftOut)
async def draft(
    incident_id: uuid.UUID,
    body: DraftIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> DraftOut:
    """Four sentences from the whole record, masked. A model words them when one may read the record and
    its wording passes Ostraka's checks; otherwise Ostraka's own draft comes back, with the reason."""
    incident = await access.load(db, user, incident_id, edit=True)
    facts = await executive.gather(db, incident)
    try:
        provider, why = ai.provider_for(incident), ""
    except ai.Unavailable as e:
        provider, why = "", str(e)
    sentences, worded, set_aside = executive.draft(facts), "ostraka", ""
    if body.ai and provider:
        log.info("executive summary drafted", extra={"incident": str(incident.id), "provider": provider})
        worded_by_model, set_aside = await _model(provider, facts)
        if worded_by_model is not None:
            sentences, worded = worded_by_model, provider
    return DraftOut(
        sentences=sentences,
        text=" ".join(sentences),
        worded=worded,
        set_aside=set_aside,
        can_ask=bool(provider),
        why_not=why,
    )


@router.put("", response_model=IncidentOut)
async def save(
    incident_id: uuid.UUID,
    body: SummaryIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> IncidentOut:
    """Save the executive summary, masked, or clear it with empty text. Whoever wrote it, it's held to the
    same rules as a model's: no lead or refuted finding called confirmed, nothing the record doesn't hold,
    no certainty, loaded or loose likelihood words. Ostraka's own draft, unchanged, always stands."""
    incident = await access.load(db, user, incident_id, edit=True)
    views.check_version(incident.version, body.version, "incident")
    if body.worded not in executive.WORDED:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Say who worded it.")
    text = mask_text(" ".join(body.text.split()))
    facts = await executive.gather(db, incident)
    own = " ".join(executive.draft(facts))
    worded = "ostraka" if text == own else ("person" if body.worded == "ostraka" else body.worded)
    if text and text != own:
        problems = executive.text_problems(facts, text)
        if problems:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "The executive summary can’t be saved: " + " ".join(problems),
            )
    if text == incident.executive_summary:
        return await views.detail(db, user, incident)
    incident.executive_summary = text
    incident.executive_summary_by = user.id if text else None
    incident.executive_summary_at = utcnow() if text else None
    incident.executive_summary_worded = worded if text else ""
    incident.version += 1
    incident.updated_at = utcnow()
    said = (
        f"{user.name} set the executive summary, worded by {WORDED_BY[worded]}"
        if text
        else f"{user.name} cleared the executive summary"
    )
    await activity.record(
        db,
        incident,
        user,
        "executive_summary_set" if text else "executive_summary_cleared",
        said,
        worded=worded,
    )
    await activity.finish(db, user)
    return await views.detail(db, user, incident)
