"""Request and response shapes for "since you left", handover notes and work on the same indicator
elsewhere."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from ostraka.incident_schemas import ActivityOut, Person


class SectionChange(BaseModel):
    entity_type: str
    count: int


class SinceOut(BaseModel):
    """What others changed on an incident after the position this person had last seen."""

    first_visit: bool
    last_viewed_at: datetime | None
    from_position: int
    through_position: int
    total: int
    changes: list[SectionChange]
    people: list[str]
    events: list[ActivityOut]


class SeenIn(BaseModel):
    """The last position the browser showed; omitted, the latest in the log."""

    position: int | None = Field(default=None, ge=0)


class HandoverSection(BaseModel):
    title: str
    lines: list[str]


class HandoverDraft(BaseModel):
    from_position: int
    through_position: int
    since: datetime
    sections: list[HandoverSection]
    text: str


class HandoverIn(BaseModel):
    """The note as the person handing over confirmed it, and the stretch of the log its draft covered."""

    body: str = Field(min_length=1, max_length=50_000)
    from_position: int = Field(ge=0)
    through_position: int = Field(ge=0)


class HandoverOut(BaseModel):
    id: uuid.UUID
    ref: str
    author: Person | None
    from_position: int
    through_position: int
    body: str
    edited: bool  # the posted text differs from what Ostraka generated
    created_at: datetime


class ElsewhereWorkOut(BaseModel):
    incident_id: uuid.UUID
    ref: str
    title: str
    people: list[str]
    editing: bool
    last_at: datetime | None


class WorkingOut(BaseModel):
    """Someone else is working this observable in another open incident."""

    item_id: uuid.UUID
    visible: list[ElsewhereWorkOut]
    hidden: int
    ask: list[str]
