"""Request and response bodies for tasks, playbooks and notifications."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ostraka.incident_schemas import Person, PromoteIn, TeamRef, Versioned, _tags
from ostraka.models import NotificationKind, RecordKind, Severity, TaskStatus, Tlp

CHECKLIST_MAX = 50


def _checklist(items: list[str]) -> list[str]:
    cleaned = [i.strip() for i in items if i.strip()]
    if len(cleaned) > CHECKLIST_MAX:
        raise ValueError(f"A checklist holds at most {CHECKLIST_MAX} items.")
    if any(len(i) > 300 for i in cleaned):
        raise ValueError("Keep each checklist item under 300 characters.")
    return cleaned


# ---- tasks ----------------------------------------------------------------------------------------


class TaskIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    parent_id: uuid.UUID | None = Field(default=None, description="Makes this a subtask, one level deep")
    description: str = Field(default="", max_length=20_000)
    team_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None
    checklist: list[str] = Field(default_factory=list)

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
    checklist: list[ChecklistItemOut]
    blocked_by: list[TaskLink]
    blocks: list[TaskLink]
    hidden_links: int  # dependencies in incidents you can't see
    playbook_id: uuid.UUID | None
    outcomes: list[str] = Field(default_factory=list, description="A decision: finished by choosing one")
    outcome: str | None = None
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


class OutcomeIn(BaseModel):
    """One way a decision can go: what it does to the record, and which steps it opens."""

    label: str = Field(min_length=1, max_length=120)
    action: Literal["none", "close", "phase", "promote"] = "none"
    phase: str | None = Field(default=None, max_length=16, description="For action phase")
    opens: list[int] = Field(default_factory=list, description="Step positions; an earlier one runs again")


class PlaybookStepIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=20_000)
    team_id: uuid.UUID | None = None
    due_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 90)
    checklist: list[str] = Field(default_factory=list)
    after: list[int] = Field(default_factory=list, description="Earlier steps, by position, to finish first")
    phase: str | None = Field(default=None, max_length=16, description="Opens when the record reaches it")
    outcomes: list[OutcomeIn] = Field(default_factory=list, max_length=6)

    @field_validator("checklist")
    @classmethod
    def _clean_checklist(cls, v: list[str]) -> list[str]:
        return _checklist(v)


class PlaybookStepOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    team: TeamRef | None
    due_minutes: int | None
    checklist: list[str]
    after: list[int] = Field(default_factory=list)
    phase: str | None = None
    outcomes: list[OutcomeIn] = Field(default_factory=list)


class WorkflowStepOut(BaseModel):
    """A step of a playbook applied to a record, and where it's got to."""

    position: int
    title: str
    team: TeamRef | None
    after: list[int]
    phase: str | None
    outcomes: list[OutcomeIn]
    state: Literal["waiting", "open", "done", "skipped"]
    conditional: bool  # opens only if a decision's outcome names it
    task_id: uuid.UUID | None
    task_ref: str | None
    outcome: str | None
    runs: int


class PlaybookIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=5000)
    trigger_tags: list[str] = Field(default_factory=list)
    applies_to: RecordKind = RecordKind.INCIDENT
    is_active: bool = True
    steps: list[PlaybookStepIn] = Field(min_length=1, max_length=50)

    @field_validator("trigger_tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        return _tags(v)


class PlaybookPatch(Versioned):
    name: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    trigger_tags: list[str] | None = None
    applies_to: RecordKind | None = None
    is_active: bool | None = None
    steps: list[PlaybookStepIn] | None = Field(default=None, min_length=1, max_length=50)

    @field_validator("trigger_tags")
    @classmethod
    def _clean_tags(cls, v: list[str] | None) -> list[str] | None:
        return _tags(v) if v is not None else None


class PlaybookOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    trigger_tags: list[str]
    applies_to: RecordKind
    is_active: bool
    steps: list[PlaybookStepOut]
    updated_at: datetime
    version: int


class AppliedPlaybookOut(BaseModel):
    playbook_id: uuid.UUID
    name: str
    applied_by: Person | None
    applied_at: datetime
    steps: list[WorkflowStepOut] = Field(default_factory=list)


class DecideIn(Versioned):
    outcome: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=5000)
    promote: PromoteIn | None = Field(default=None, description="The incident, for an outcome that promotes")


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
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("A push endpoint is an https:// URL from the browser.")
        return v


class PushEndpoint(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)


class TeamWebhookIn(BaseModel):
    url: str = Field(default="", max_length=2000, description="Empty disconnects the channel")
