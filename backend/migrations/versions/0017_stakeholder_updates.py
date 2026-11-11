"""Stakeholder updates: each incident's update rhythm, its stakeholders (people, outside email
addresses, Teams channels), the updates themselves and every revision of a draft.

The two new notification kinds need no change here: kinds are stored as checked strings.

Revision ID: 0017
Revises: 0016
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "update_plans",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("rhythm_set", sa.Boolean(), nullable=False),
        sa.Column("every_minutes", sa.Integer(), nullable=True),
        sa.Column("nudged_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("set_by", sa.Uuid(), nullable=True),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_update_plans_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], name=op.f("fk_update_plans_set_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("incident_id", name=op.f("pk_update_plans")),
    )
    op.create_table(
        "stakeholders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(user_id IS NOT NULL)::int + (email IS NOT NULL)::int + (team_id IS NOT NULL)::int = 1",
            name=op.f("ck_stakeholders_one_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["added_by"], ["users.id"], name=op.f("fk_stakeholders_added_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_stakeholders_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_stakeholders_team_id_teams"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_stakeholders_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stakeholders")),
        sa.UniqueConstraint("incident_id", "email", name="uq_stakeholders_email"),
        sa.UniqueConstraint("incident_id", "team_id", name="uq_stakeholders_team"),
        sa.UniqueConstraint("incident_id", "user_id", name="uq_stakeholders_user"),
    )
    op.create_index(op.f("ix_stakeholders_incident_id"), "stakeholders", ["incident_id"], unique=False)
    op.create_table(
        "stakeholder_updates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("draft", "sent", "discarded", name="update_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), nullable=True),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("rephrased", sa.JSON(), nullable=True),
        sa.Column("rephrased_by", sa.String(length=40), nullable=False),
        sa.Column("use_rephrased", sa.Boolean(), nullable=False),
        sa.Column("drafted_by", sa.Uuid(), nullable=True),
        sa.Column("drafted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["users.id"],
            name=op.f("fk_stakeholder_updates_approved_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["assessments.id"],
            name=op.f("fk_stakeholder_updates_assessment_id_assessments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["drafted_by"],
            ["users.id"],
            name=op.f("fk_stakeholder_updates_drafted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_stakeholder_updates_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stakeholder_updates")),
        sa.UniqueConstraint("incident_id", "number", name="uq_stakeholder_updates_number"),
        sa.UniqueConstraint("seq", name=op.f("uq_stakeholder_updates_seq")),
    )
    op.create_index(
        op.f("ix_stakeholder_updates_incident_id"), "stakeholder_updates", ["incident_id"], unique=False
    )
    op.create_index(
        "uq_stakeholder_updates_one_draft",
        "stakeholder_updates",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.create_table(
        "update_revisions",
        sa.Column("update_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("change", sa.String(length=40), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("by", sa.Uuid(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["by"], ["users.id"], name=op.f("fk_update_revisions_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["update_id"],
            ["stakeholder_updates.id"],
            name=op.f("fk_update_revisions_update_id_stakeholder_updates"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("update_id", "version", name=op.f("pk_update_revisions")),
    )


def downgrade() -> None:
    op.drop_table("update_revisions")
    op.drop_index("uq_stakeholder_updates_one_draft", table_name="stakeholder_updates")
    op.drop_index(op.f("ix_stakeholder_updates_incident_id"), table_name="stakeholder_updates")
    op.drop_table("stakeholder_updates")
    op.drop_index(op.f("ix_stakeholders_incident_id"), table_name="stakeholders")
    op.drop_table("stakeholders")
    op.drop_table("update_plans")
