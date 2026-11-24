"""Each record's executive summary: four sentences, drafted by Ostraka (or worded by a model and checked),
then saved by a person.

Revision ID: 0031
Revises: 0030
Created: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("executive_summary", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "incidents",
        sa.Column("executive_summary_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.add_column("incidents", sa.Column("executive_summary_at", sa.DateTime(timezone=True)))
    # who worded it: ostraka (the fixed rules), ollama or anthropic (checked), or person
    op.add_column(
        "incidents",
        sa.Column("executive_summary_worded", sa.String(16), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("incidents", "executive_summary_worded")
    op.drop_column("incidents", "executive_summary_at")
    op.drop_column("incidents", "executive_summary_by")
    op.drop_column("incidents", "executive_summary")
