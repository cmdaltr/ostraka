"""Who accepted the commercial licence terms when a licence was installed, and which version.
Licences installed before this have none recorded.

Revision ID: 0012
Revises: 0011
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "licences", sa.Column("terms_version", sa.String(length=16), nullable=False, server_default="")
    )
    op.add_column("licences", sa.Column("accepted_by", sa.Uuid(), nullable=True))
    op.add_column(
        "licences", sa.Column("accepted_by_name", sa.String(length=200), nullable=False, server_default="")
    )
    op.add_column("licences", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_licences_accepted_by_users"),
        "licences",
        "users",
        ["accepted_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_licences_accepted_by_users"), "licences", type_="foreignkey")
    op.drop_column("licences", "accepted_at")
    op.drop_column("licences", "accepted_by_name")
    op.drop_column("licences", "accepted_by")
    op.drop_column("licences", "terms_version")
