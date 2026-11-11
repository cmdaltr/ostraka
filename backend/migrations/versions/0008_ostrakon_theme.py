"""The rename to ostraka: the Cryptex palette becomes Ostrakon, so anyone who chose it keeps it.

Revision ID: 0008
Revises: 0007
Created: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE users SET theme_palette = 'ostrakon' WHERE theme_palette = 'cryptex'")


def downgrade() -> None:
    op.execute("UPDATE users SET theme_palette = 'cryptex' WHERE theme_palette = 'ostrakon'")
