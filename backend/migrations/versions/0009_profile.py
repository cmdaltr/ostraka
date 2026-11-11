"""The profile page: job title, out-of-hours phone, time zone and availability; a replacement
authenticator waiting for its first code; quiet hours, kinds kept to the bell, and new-device alerts.

Revision ID: 0009
Revises: 0008
Created: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = [
    sa.Column("job_title", sa.String(120), nullable=False, server_default=""),
    sa.Column("phone", sa.String(40), nullable=False, server_default=""),
    sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/London"),
    sa.Column(
        "availability",
        sa.Enum("available", "busy", "away", name="availability", native_enum=False, length=32),
        nullable=False,
        server_default="available",
    ),
    sa.Column("pending_totp_secret", sa.Text(), nullable=True),
    sa.Column("muted_kinds", postgresql.ARRAY(sa.String(32)), nullable=False, server_default="{}"),
    sa.Column("quiet_from", sa.String(5), nullable=True),
    sa.Column("quiet_until", sa.String(5), nullable=True),
    sa.Column("new_device_alerts", sa.Boolean(), nullable=False, server_default=sa.true()),
]


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("users", column)


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("users", column.name)
