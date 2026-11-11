"""Regulator clocks: a regime's deadlines on an incident, with an owner, and each milestone's record
of being met and nudged.

Revision ID: 0015
Revises: 0013
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clocks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column(
            "regime",
            sa.Enum(
                "uk_gdpr",
                "uk_nis",
                "eu_nis2",
                "dora",
                "custom",
                name="clock_regime",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("milestones", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.Uuid(), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_clocks_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_clocks_owner_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["closed_by"], ["users.id"], name=op.f("fk_clocks_closed_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_clocks_created_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clocks")),
        sa.UniqueConstraint("seq", name=op.f("uq_clocks_seq")),
    )
    op.create_index(op.f("ix_clocks_incident_id"), "clocks", ["incident_id"], unique=False)
    op.create_index(op.f("ix_clocks_owner_id"), "clocks", ["owner_id"], unique=False)
    op.create_table(
        "clock_milestones",
        sa.Column("clock_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("met_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("met_by", sa.Uuid(), nullable=True),
        sa.Column("reference", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nudged", sa.Integer(), nullable=False),
        sa.Column("nudged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["clock_id"], ["clocks.id"], name=op.f("fk_clock_milestones_clock_id_clocks"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["met_by"], ["users.id"], name=op.f("fk_clock_milestones_met_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("clock_id", "key", name=op.f("pk_clock_milestones")),
    )


def downgrade() -> None:
    op.drop_table("clock_milestones")
    op.drop_index(op.f("ix_clocks_owner_id"), table_name="clocks")
    op.drop_index(op.f("ix_clocks_incident_id"), table_name="clocks")
    op.drop_table("clocks")
