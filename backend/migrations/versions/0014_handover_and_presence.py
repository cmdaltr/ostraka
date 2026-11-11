"""Handover and presence: where each person last left an incident, posted shift-handover notes, and
versions on the records that were still edited without one (comments, checklist items, teams and
accounts).

Revision ID: 0014
Revises: 0013
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VERSIONED = ("comments", "checklist_items", "teams", "users")


def upgrade() -> None:
    op.create_table(
        "incident_views",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("last_position", sa.Integer(), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_views_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_incident_views_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("incident_id", "user_id", name=op.f("pk_incident_views")),
    )
    op.create_table(
        "handover_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("from_position", sa.Integer(), nullable=False),
        sa.Column("through_position", sa.Integer(), nullable=False),
        sa.Column("generated", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_handover_notes_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("fk_handover_notes_author_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handover_notes")),
        sa.UniqueConstraint("seq", name=op.f("uq_handover_notes_seq")),
    )
    op.create_index(op.f("ix_handover_notes_incident_id"), "handover_notes", ["incident_id"], unique=False)
    for table in VERSIONED:
        op.add_column(table, sa.Column("version", sa.Integer(), server_default="1", nullable=False))
        op.alter_column(table, "version", server_default=None)


def downgrade() -> None:
    for table in VERSIONED:
        op.drop_column(table, "version")
    op.drop_index(op.f("ix_handover_notes_incident_id"), table_name="handover_notes")
    op.drop_table("handover_notes")
    op.drop_table("incident_views")
