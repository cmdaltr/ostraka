"""Report exports: the technical report or the briefing, as PDF or Word.

A report leaves ostraka, so exporting is for people who worked the incident, and a sensitive
incident's reports are its commander's alone (`policy.can_export`). Every export is written to the
security log and to the incident's own activity chain.

Reports are in Ostrakon unless the licence allows a brand (`reports.brands.styling_for`).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ostraka import audit
from ostraka.auth.deps import current_user
from ostraka.db import get_session
from ostraka.incidents import access, activity
from ostraka.models import User
from ostraka.reports import branded, theme
from ostraka.reports import incident as content
from ostraka.reports.brands import styling_for

router = APIRouter(prefix="/api/incidents/{incident_id}", tags=["reports"])

MEDIA = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
VARIANT_WORDS = {"report": "technical report", "briefing": "briefing"}


@router.get("/export", response_class=Response)
async def export(
    incident_id: uuid.UUID,
    request: Request,
    variant: Literal["report", "briefing"] = Query(default="report"),
    fmt: Literal["pdf", "docx"] = Query(default="pdf", alias="format"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    incident = await access.load(db, user, incident_id)
    if why := await access.export_refusal(db, user, incident):
        raise HTTPException(status.HTTP_403_FORBIDDEN, why)

    record = await content.gather(db, incident, user)
    styling = await styling_for(db, incident)
    with theme.use(styling.theme):
        cover, blocks = content.technical(record) if variant == "report" else content.briefing(record)
        cover.prepared_for = styling.prepared_for or cover.prepared_for
        # Rendering is CPU-bound for a second or two; keep it off the event loop. The thread gets a
        # copy of this context, theme included.
        body = await asyncio.to_thread(branded.render, cover, blocks, fmt)

    ip = request.client.host if request.client else ""
    audit.record(
        db,
        "report_exported",
        actor_id=user.id,
        ip=ip,
        incident=incident.ref,
        variant=variant,
        format=fmt,
        brand=styling.brand or None,
    )
    await activity.record(
        db,
        incident,
        user,
        "report_exported",
        f"{user.name} exported the {VARIANT_WORDS[variant]} as {fmt.upper()}",
        variant=variant,
        format=fmt,
        tlp=incident.tlp.value,
    )
    await activity.finish(db, user)
    name = f"{incident.ref}-{variant}.{fmt}"
    return Response(
        body,
        media_type=MEDIA[fmt],
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )
