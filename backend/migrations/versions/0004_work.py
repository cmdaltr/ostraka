"""Work: tasks and checklists, playbooks, notifications, push subscriptions, team channels and
notification preferences.

Revision ID: 0004
Revises: 0003
Created: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playbooks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("trigger_tags", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_playbooks_created_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playbooks")),
        sa.UniqueConstraint("name", name=op.f("uq_playbooks_name")),
    )
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=200), nullable=False),
        sa.Column("auth", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_push_subscriptions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_subscriptions")),
        sa.UniqueConstraint("endpoint", name=op.f("uq_push_subscriptions_endpoint")),
    )
    op.create_index(op.f("ix_push_subscriptions_user_id"), "push_subscriptions", ["user_id"], unique=False)
    op.create_table(
        "incident_playbooks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("playbook_id", sa.Uuid(), nullable=False),
        sa.Column("applied_by", sa.Uuid(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["applied_by"],
            ["users.id"],
            name=op.f("fk_incident_playbooks_applied_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_playbooks_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["playbook_id"],
            ["playbooks.id"],
            name=op.f("fk_incident_playbooks_playbook_id_playbooks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_playbooks")),
        sa.UniqueConstraint("incident_id", "playbook_id", name=op.f("uq_incident_playbooks_incident_id")),
    )
    op.create_index(
        op.f("ix_incident_playbooks_incident_id"), "incident_playbooks", ["incident_id"], unique=False
    )
    op.create_table(
        "playbook_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("playbook_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("due_minutes", sa.Integer(), nullable=True),
        sa.Column("checklist", postgresql.ARRAY(sa.String(length=300)), nullable=False),
        sa.ForeignKeyConstraint(
            ["playbook_id"],
            ["playbooks.id"],
            name=op.f("fk_playbook_steps_playbook_id_playbooks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_playbook_steps_team_id_teams"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playbook_steps")),
    )
    op.create_index(op.f("ix_playbook_steps_playbook_id"), "playbook_steps", ["playbook_id"], unique=False)
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "in_progress",
                "blocked",
                "done",
                "cancelled",
                name="task_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("blocked_reason", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("playbook_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_by", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reminded_due_soon_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminded_overdue_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_lead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_commander_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("team_id IS NOT NULL OR assignee_id IS NOT NULL", name=op.f("ck_tasks_has_owner")),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["users.id"], name=op.f("fk_tasks_assignee_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["completed_by"], ["users.id"], name=op.f("fk_tasks_completed_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_tasks_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], name=op.f("fk_tasks_incident_id_incidents"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["playbook_id"],
            ["playbooks.id"],
            name=op.f("fk_tasks_playbook_id_playbooks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_tasks_team_id_teams"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint("seq", name=op.f("uq_tasks_seq")),
    )
    op.create_index(op.f("ix_tasks_assignee_id"), "tasks", ["assignee_id"], unique=False)
    op.create_index(op.f("ix_tasks_incident_id"), "tasks", ["incident_id"], unique=False)
    op.create_index(op.f("ix_tasks_team_id"), "tasks", ["team_id"], unique=False)
    op.create_table(
        "checklist_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("done_by", sa.Uuid(), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["done_by"], ["users.id"], name=op.f("fk_checklist_items_done_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_checklist_items_task_id_tasks"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checklist_items")),
    )
    op.create_index(op.f("ix_checklist_items_task_id"), "checklist_items", ["task_id"], unique=False)
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "assigned",
                "team_task",
                "due_soon",
                "overdue",
                "escalated",
                "nudge",
                "mention",
                "reply",
                "commander",
                name="notification_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("incident_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_notifications_actor_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_notifications_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_notifications_task_id_tasks"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_notifications_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(op.f("ix_notifications_created_at"), "notifications", ["created_at"], unique=False)
    op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "read_at"], unique=False)
    # Existing rows need a value: no channel, and both notification routes on
    op.add_column("teams", sa.Column("teams_webhook", sa.Text(), nullable=False, server_default=""))
    op.add_column("users", sa.Column("notify_email", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("notify_push", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("users", "notify_push")
    op.drop_column("users", "notify_email")
    op.drop_column("teams", "teams_webhook")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index(op.f("ix_notifications_created_at"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_checklist_items_task_id"), table_name="checklist_items")
    op.drop_table("checklist_items")
    op.drop_index(op.f("ix_tasks_team_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_incident_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_assignee_id"), table_name="tasks")
    op.drop_table("tasks")
    op.drop_index(op.f("ix_playbook_steps_playbook_id"), table_name="playbook_steps")
    op.drop_table("playbook_steps")
    op.drop_index(op.f("ix_incident_playbooks_incident_id"), table_name="incident_playbooks")
    op.drop_table("incident_playbooks")
    op.drop_index(op.f("ix_push_subscriptions_user_id"), table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
    op.drop_table("playbooks")
