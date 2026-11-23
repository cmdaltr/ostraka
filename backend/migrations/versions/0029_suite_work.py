"""The suite starts work: Shikari hunt runs started from a record, with their detections and counts.

Pairing and Rowanholt's webhooks need no new columns (an integration already holds a webhook secret).

Revision ID: 0029
Revises: 0028
Created: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shikari_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "incident_id", sa.Uuid(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("target", sa.String(500), nullable=False),
        sa.Column("execute", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("group_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("error", sa.String(300), nullable=False, server_default=""),
        sa.Column("detections", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("counts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("totals", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("started_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", name="uq_shikari_runs_run_id"),
    )
    op.create_index("ix_shikari_runs_incident_id", "shikari_runs", ["incident_id"])


def downgrade() -> None:
    op.drop_index("ix_shikari_runs_incident_id", table_name="shikari_runs")
    op.drop_table("shikari_runs")
