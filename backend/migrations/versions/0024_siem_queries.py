"""SIEM queries: the connections an admin adds, and every query run through them, found again by any part
of its text (pg_trgm). Only the query is kept, never what it returned.

Revision ID: 0024
Revises: 0023
Created: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("splunk", "elastic", "defender", "secops")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "siem_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("kind", sa.Enum(*KINDS, name="siem_kind", native_enum=False, length=32), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False, server_default=""),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_ok", sa.Boolean(), nullable=True),
        sa.Column("check_message", sa.String(300), nullable=False, server_default=""),
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "siem_queries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False, unique=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("kind", sa.Enum(*KINDS, name="siem_kind", native_enum=False, length=32), nullable=False),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("siem_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("connection_name", sa.String(80), nullable=False),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome",
            sa.Enum("ok", "failed", name="query_outcome", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("run_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_siem_queries_incident_id", "siem_queries", ["incident_id"])
    op.create_index("ix_siem_queries_run_at", "siem_queries", ["run_at"])
    op.create_index(
        "ix_siem_queries_text_trgm",
        "siem_queries",
        ["text"],
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_siem_queries_text_trgm", table_name="siem_queries")
    op.drop_index("ix_siem_queries_run_at", table_name="siem_queries")
    op.drop_index("ix_siem_queries_incident_id", table_name="siem_queries")
    op.drop_table("siem_queries")
    op.drop_table("siem_connections")
    # pg_trgm stays: dropping an extension other things may use isn't this revision's to do
