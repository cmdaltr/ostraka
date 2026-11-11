"""The work in an incident: tasks with checklists, the playbooks that create them, and the
notifications and push subscriptions that tell people about them."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ostraka.db import Base
from ostraka.models.incidents import RecordKind
from ostraka.models.people import Team, User, _enum, utcnow


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


FINISHED = (TaskStatus.DONE, TaskStatus.CANCELLED)


class NotificationKind(StrEnum):
    ASSIGNED = "assigned"  # a task is yours
    TEAM_TASK = "team_task"  # a task waits in your team's queue
    DUE_SOON = "due_soon"
    OVERDUE = "overdue"
    ESCALATED = "escalated"  # someone else's task is overdue and you're its lead or commander
    NUDGE = "nudge"  # a person asked you to look at something
    MENTION = "mention"
    REPLY = "reply"
    COMMANDER = "commander"  # you're now incident commander
    UNBLOCKED = "unblocked"  # a task you hold was waiting on another, which has finished
    NEW_DEVICE = "new_device"  # your account signed in from a browser it hasn't used before


class Task(Base):
    """Work assigned to a team (it waits in the queue until someone claims it) or to a person.
    A claimed team task keeps its team, so releasing it returns it to the queue."""

    __tablename__ = "tasks"
    __table_args__ = (CheckConstraint("team_id IS NOT NULL OR assignee_id IS NOT NULL", name="has_owner"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)  # TSK-31
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"), index=True)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[TaskStatus] = mapped_column(_enum(TaskStatus, "task_status"), default=TaskStatus.OPEN)
    blocked_reason: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    playbook_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("playbooks.id", ondelete="SET NULL"))
    # A playbook step's task: which applied playbook made it, and which step it is
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incident_playbooks.id", ondelete="SET NULL"))
    step: Mapped[int | None] = mapped_column(Integer)
    # A decision: it's finished by choosing one of these, not by marking it done
    outcomes: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list)
    outcome: Mapped[str | None] = mapped_column(String(120))

    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)

    # What the scheduler has already sent, so each reminder goes once (or once per interval)
    reminded_due_soon_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminded_overdue_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_lead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_commander_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    checklist: Mapped[list[ChecklistItem]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ChecklistItem.position"
    )
    team: Mapped[Team | None] = relationship(lazy="joined")
    assignee: Mapped[User | None] = relationship(lazy="joined", foreign_keys=[assignee_id])

    @property
    def ref(self) -> str:
        return f"TSK-{self.seq}"


class TaskDependency(Base):
    """A task that can't finish until another does, in any incident."""

    __tablename__ = "task_dependencies"

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    blocked_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(300))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Playbook(Base):
    """A set of tasks to start an incident with. Trigger tags suggest it in the declare form."""

    __tablename__ = "playbooks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    trigger_tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    applies_to: Mapped[RecordKind] = mapped_column(
        _enum(RecordKind, "record_kind"), default=RecordKind.INCIDENT
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    steps: Mapped[list[PlaybookStep]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="PlaybookStep.position"
    )


class PlaybookStep(Base):
    """One task a playbook creates. With no team, the task goes to whoever applies the playbook.

    A step opens (its task is created) when everything in `after` has finished, when the record
    has reached `phase`, and, if a decision's outcome names it, when that outcome is chosen. A step
    with `outcomes` is a decision; each outcome may act on the record and open other steps,
    including earlier ones, which run again."""

    __tablename__ = "playbook_steps"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    playbook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("playbooks.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    due_minutes: Mapped[int | None] = mapped_column(Integer)  # after the playbook is applied
    checklist: Mapped[list[str]] = mapped_column(ARRAY(String(300)), default=list)
    after: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)  # positions of earlier steps
    phase: Mapped[str | None] = mapped_column(String(16))
    # [{"label", "action": none|close|phase|promote, "phase", "opens": [positions]}]
    outcomes: Mapped[list[dict]] = mapped_column(JSONB, default=list)


class IncidentPlaybook(Base):
    """A playbook applied to an incident: a copy of its steps as they were, each with its state
    (waiting, open, done or skipped), its task and any outcome. Editing the playbook later doesn't
    change a run already under way."""

    __tablename__ = "incident_playbooks"
    __table_args__ = (UniqueConstraint("incident_id", "playbook_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    playbook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("playbooks.id", ondelete="CASCADE"))
    applied_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    steps: Mapped[list[dict]] = mapped_column(JSONB, default=list)


class Notification(Base):
    """Something one person should know about. Text is stored as written and masked when read."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_unread", "user_id", "read_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[NotificationKind] = mapped_column(_enum(NotificationKind, "notification_kind"))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(500), default="")  # a path within Ostraka
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PushSubscription(Base):
    """A browser that asked for push notifications (the Web Push endpoint and its keys)."""

    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
