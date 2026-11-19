"""Request and response bodies for tasks, playbooks and notifications."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from ostraka.incident_schemas import Person, TeamRef, Versioned, _tags
from ostraka.models import NotificationKind, RecordKind, Severity, TaskStatus, Tlp
from ostraka.notify import push

CHECKLIST_MAX = 50


def _checklist(items: list[str]) -> list[str]:
    cleaned = [i.strip() for i in items if i.strip()]
    if len(cleaned) > CHECKLIST_MAX:
        raise ValueError(f"A checklist holds at most {CHECKLIST_MAX} items.")
    if any(len(i) > 300 for i in cleaned):
        raise ValueError("Keep each checklist item under 300 characters.")
    return cleaned


# ---- tasks ----------------------------------------------------------------------------------------


def _known_stage(v: str | None) -> str | None:
    from ostraka.flow import BY_KEY

    if v is None or v == "":
        return None
    if v not in BY_KEY:
        raise ValueError(f"No attack-flow step is called {v}.")
    return v


class TaskIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    parent_id: uuid.UUID | None = Field(default=None, description="Makes this a subtask, one level deep")
    description: str = Field(default="", max_length=20_000)
    team_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None
    stage: str | None = Field(
        default=None, description="The attack-flow step it answers (ostraka.flow.STAGES)"
    )
    checklist: list[str] = Field(default_factory=list)

    @field_validator("stage")
    @classmethod
    def _stage(cls, v: str | None) -> str | None:
        return _known_stage(v)

    @field_validator("checklist")
    @classmethod
    def _clean_checklist(cls, v: list[str]) -> list[str]:
        return _checklist(v)

    @model_validator(mode="after")
    def _owner(self) -> TaskIn:
        if self.team_id is None and self.assignee_id is None:
            raise ValueError("Assign the task to a team or a person.")
        return self


class TaskPatch(Versioned):
    """Fields left out stay as they are; `due_at`, `team_id` and `assignee_id` sent as null are cleared."""

    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    team_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None
    status: TaskStatus | None = None
    blocked_reason: str | None = Field(default=None, max_length=2000)
    stage: str | None = None

    @field_validator("stage")
    @classmethod
    def _stage(cls, v: str | None) -> str | None:
        return _known_stage(v)

    @model_validator(mode="after")
    def _blocked(self) -> TaskPatch:
        if self.status is TaskStatus.BLOCKED and not (self.blocked_reason or "").strip():
            raise ValueError("Say what the task is blocked on.")
        return self


class ChecklistItemIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)


class ChecklistItemPatch(BaseModel):
    version: int  # each item has its own, so two people ticking different items never collide
    text: str | None = Field(default=None, min_length=1, max_length=300)
    done: bool | None = None


class ChecklistItemOut(BaseModel):
    id: uuid.UUID
    text: str
    done: bool
    done_by: Person | None
    done_at: datetime | None
    version: int = 1


class TaskLink(BaseModel):
    """Another task this one waits on, or that waits on it."""

    id: uuid.UUID
    ref: str
    title: str
    status: TaskStatus
    incident_ref: str


class TaskOut(BaseModel):
    id: uuid.UUID
    ref: str
    incident_id: uuid.UUID
    parent_id: uuid.UUID | None
    title: str
    description: str
    team: TeamRef | None
    assignee: Person | None
    status: TaskStatus
    blocked_reason: str
    due_at: datetime | None
    overdue: bool
    stage: str | None = None
    checklist: list[ChecklistItemOut]
    blocked_by: list[TaskLink]
    blocks: list[TaskLink]
    hidden_links: int  # dependencies in incidents you can't see
    playbook_id: uuid.UUID | None
    created_by: Person | None
    created_at: datetime
    updated_at: datetime
    completed_by: Person | None
    completed_at: datetime | None
    version: int


class IncidentBrief(BaseModel):
    id: uuid.UUID
    kind: RecordKind
    ref: str
    title: str
    severity: Severity
    tlp: Tlp


class MyTaskOut(TaskOut):
    incident: IncidentBrief
    queued: bool  # waiting in your team's queue rather than assigned to you


class BlockerIn(BaseModel):
    ref: str = Field(pattern=r"^#?TSK-\d+$", description="The task this one waits on, e.g. TSK-12")


class NudgeIn(BaseModel):
    message: str = Field(default="", max_length=500)


# ---- playbooks ------------------------------------------------------------------------------------


class PlaybookCheckIn(BaseModel):
    part: str = Field(
        default="any", max_length=32, description="A part of the attack (ostraka.playbook.PARTS)"
    )
    title: str = Field(min_length=3, max_length=200)
    team_id: uuid.UUID | None = None


class PlaybookCheckOut(BaseModel):
    id: uuid.UUID
    part: str
    title: str
    team: TeamRef | None


def _conditions(v: dict[str, str] | None) -> dict[str, str] | None:
    """Only if…: words for a part, trimmed; an empty one is no condition."""
    if v is None:
        return None
    if any(len(words) > 200 for words in v.values()):
        raise ValueError("An “only if” is at most 200 characters.")
    return {part: words.strip() for part, words in v.items() if words.strip()}


class PlaybookIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=5000)
    trigger_tags: list[str] = Field(default_factory=list)
    applies_to: RecordKind = RecordKind.INCIDENT
    is_active: bool = True
    conditions: dict[str, str] = Field(default_factory=dict, description="Part: only if…")
    checks: list[PlaybookCheckIn] = Field(min_length=1, max_length=100)

    @field_validator("trigger_tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        return _tags(v)

    @field_validator("conditions")
    @classmethod
    def _clean_conditions(cls, v: dict[str, str]) -> dict[str, str]:
        return _conditions(v) or {}


class PlaybookPatch(Versioned):
    name: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    trigger_tags: list[str] | None = None
    applies_to: RecordKind | None = None
    is_active: bool | None = None
    conditions: dict[str, str] | None = None
    checks: list[PlaybookCheckIn] | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("trigger_tags")
    @classmethod
    def _clean_tags(cls, v: list[str] | None) -> list[str] | None:
        return _tags(v) if v is not None else None

    @field_validator("conditions")
    @classmethod
    def _clean_conditions(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _conditions(v)


class PlaybookOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    trigger_tags: list[str]
    applies_to: RecordKind
    is_active: bool
    conditions: dict[str, str]
    checks: list[PlaybookCheckOut]
    updated_at: datetime
    version: int


class ApplyIn(BaseModel):
    parts: list[str] | None = Field(
        default=None, max_length=40, description="The parts to take; omitted, every part without an only if"
    )


class AppliedPlaybookOut(BaseModel):
    """A playbook on a record: the parts taken, and those it has checks for that haven't been."""

    playbook_id: uuid.UUID
    name: str
    applied_by: Person | None
    applied_at: datetime
    parts: list[str]
    remaining: list[str]
    conditions: dict[str, str]


# ---- notifications --------------------------------------------------------------------------------


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: NotificationKind
    title: str
    body: str
    link: str
    actor: Person | None
    created_at: datetime
    read_at: datetime | None


class NotificationsOut(BaseModel):
    items: list[NotificationOut]
    unread: int


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=200)
    auth: str = Field(min_length=8, max_length=64)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)
    keys: PushKeys
    user_agent: str = Field(default="", max_length=300)

    @field_validator("endpoint")
    @classmethod
    def _push_service(cls, v: str) -> str:
        if problem := push.endpoint_problem(v):
            raise ValueError(problem)
        return v


class PushEndpoint(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)


class TeamWebhookIn(BaseModel):
    url: str = Field(default="", max_length=2000, description="Empty disconnects the channel")
