"""The installation's MISP instance, for pushing incidents as MISP events.

Revision ID: 0019
Revises: 0013
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "misp_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("verify_tls", sa.Boolean(), nullable=False),
        sa.Column("distribution", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_misp_settings_one_row")),
        sa.CheckConstraint("distribution BETWEEN 0 AND 3", name=op.f("ck_misp_settings_distribution")),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_misp_settings_updated_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_misp_settings")),
    )


def downgrade() -> None:
    op.drop_table("misp_settings")
