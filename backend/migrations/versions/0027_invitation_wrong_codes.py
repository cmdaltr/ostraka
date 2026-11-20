"""Invitations count wrong authenticator codes, so a link closes after five (the account standard,
item 7). Going back, the count goes; a link closed by it stays revoked.

Revision ID: 0027
Revises: 0026
Created: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invitations", sa.Column("wrong_codes", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("invitations", "wrong_codes")
