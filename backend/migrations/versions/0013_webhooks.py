"""Webhooks (the `automation` feature) and the record of their recent deliveries.

Revision ID: 0013
Revises: 0012
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("events", postgresql.ARRAY(sa.String(length=40)), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_webhooks_created_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhooks")),
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("webhook_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=200), nullable=False),
        sa.Column("tries", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["webhook_id"],
            ["webhooks.id"],
            name=op.f("fk_webhook_deliveries_webhook_id_webhooks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_deliveries")),
    )
    op.create_index(
        op.f("ix_webhook_deliveries_webhook_id"), "webhook_deliveries", ["webhook_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_webhook_deliveries_webhook_id"), table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
