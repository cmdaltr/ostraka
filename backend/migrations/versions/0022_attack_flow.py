"""The attack flow: analysts' decisions on its stages, the stage a task answers, and the ATT&CK group a
record is attributed to.

Revision ID: 0022
Revises: 0021
Created: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "flow_stages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "incident_id", sa.Uuid(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column(
            "call",
            sa.Enum("expected", "dismissed", "cleared", name="stage_call", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("incident_id", "stage", name="uq_flow_stages_incident_stage"),
    )
    op.create_index("ix_flow_stages_incident_id", "flow_stages", ["incident_id"])
    op.add_column("tasks", sa.Column("stage", sa.String(32), nullable=True))
    op.add_column("incidents", sa.Column("attributed_group", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "attributed_group")
    op.drop_column("tasks", "stage")
    op.drop_index("ix_flow_stages_incident_id", table_name="flow_stages")
    op.drop_table("flow_stages")
