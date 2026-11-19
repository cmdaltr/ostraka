"""Sign-in lockout is kept per account and address in Redis (`ostraka.auth.lockout`), so the account's own
count and lock go. Going back, every account starts unlocked with no failures counted.

Revision ID: 0026
Revises: 0025
Created: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_logins")


def downgrade() -> None:
    op.add_column("users", sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("users", "failed_logins", server_default=None)
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
