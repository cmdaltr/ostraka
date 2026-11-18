"""Every incident and hunt has a code name: two plain English words ("Folded Lantern"), unique, that say
nothing about the case. Records already open are given one each.

Revision ID: 0021
Revises: 0020
Created: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ostraka import codenames

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("codename", sa.String(60), nullable=True))
    conn = op.get_bind()
    taken: set[str] = set()
    name = sa.text("UPDATE incidents SET codename = :name WHERE id = :id")
    for (record_id,) in conn.execute(sa.text("SELECT id FROM incidents ORDER BY declared_at")):
        chosen = codenames.generate(taken)
        taken.add(chosen)
        conn.execute(name, {"name": chosen, "id": record_id})
    op.alter_column("incidents", "codename", nullable=False)
    op.create_unique_constraint("uq_incidents_codename", "incidents", ["codename"])


def downgrade() -> None:
    op.drop_constraint("uq_incidents_codename", "incidents", type_="unique")
    op.drop_column("incidents", "codename")
