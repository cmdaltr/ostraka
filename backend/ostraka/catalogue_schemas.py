"""Response bodies for the catalogue of closed records and the lessons register."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from ostraka.analysis_schemas import LessonOut
from ostraka.incident_schemas import ArchiveRequestOut, IncidentSummary
from ostraka.models import ArchiveState, RecordKind


class CatalogueEntry(IncidentSummary):
    closed_at: datetime | None
    archived_at: datetime | None
    cold_at: datetime | None
    techniques: list[str]  # from its findings that weren't refuted


class CataloguePage(BaseModel):
    items: list[CatalogueEntry]
    total: int
    has_more: bool
    page: int
    per_page: int


class RecordRef(BaseModel):
    id: uuid.UUID
    ref: str
    kind: RecordKind
    codename: str
    title: str
    archive_state: ArchiveState
    closed_at: datetime | None


class RegisterLesson(LessonOut):
    record: RecordRef


class LessonPage(BaseModel):
    items: list[RegisterLesson]
    total: int | None  # None: an observer's masked search, not counted to the end yet
    has_more: bool
    page: int
    per_page: int


class WaitingRequest(BaseModel):
    record: RecordRef
    request: ArchiveRequestOut
