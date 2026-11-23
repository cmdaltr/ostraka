"""The catalogue and archiving: closed records archived after 30 days and put in cold store after 90,
each record's own setting, requests to restore one or archive one early, and indexes for searching
closed records.

The two new notification kinds need no change here: kinds are stored as checked strings.

Revision ID: 0030
Revises: 0029
Created: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column(
        "incidents",
        sa.Column("archive_state", sa.String(32), nullable=False, server_default="active"),
    )
    op.add_column(
        "incidents", sa.Column("auto_archive", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "incidents", sa.Column("archive_after_days", sa.Integer(), nullable=False, server_default="30")
    )
    op.add_column(
        "incidents", sa.Column("cold_after_days", sa.Integer(), nullable=False, server_default="90")
    )
    for name in ("archived_at", "cold_at", "restored_at"):
        op.add_column("incidents", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_incidents_archive_days",
        "incidents",
        "archive_after_days >= 1 AND cold_after_days > archive_after_days AND cold_after_days <= 3650",
    )
    op.create_index("ix_incidents_closed", "incidents", ["status", "closed_at"])
    for column in ("title", "codename"):
        op.create_index(
            f"ix_incidents_{column}_trgm",
            "incidents",
            [column],
            postgresql_using="gin",
            postgresql_ops={column: "gin_trgm_ops"},
        )
    op.create_index("ix_incidents_tags", "incidents", ["tags"], postgresql_using="gin")

    op.create_table(
        "archive_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "incident_id", sa.Uuid(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("from_state", sa.String(32), nullable=False),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_archive_requests_incident_id", "archive_requests", ["incident_id"])
    op.create_index(
        "uq_archive_requests_pending",
        "archive_requests",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text("outcome = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_archive_requests_pending", table_name="archive_requests")
    op.drop_index("ix_archive_requests_incident_id", table_name="archive_requests")
    op.drop_table("archive_requests")
    op.drop_index("ix_incidents_tags", table_name="incidents")
    for column in ("title", "codename"):
        op.drop_index(f"ix_incidents_{column}_trgm", table_name="incidents")
    op.drop_index("ix_incidents_closed", table_name="incidents")
    op.drop_constraint("ck_incidents_archive_days", "incidents", type_="check")
    for name in (
        "restored_at",
        "cold_at",
        "archived_at",
        "cold_after_days",
        "archive_after_days",
        "auto_archive",
        "archive_state",
    ):
        op.drop_column("incidents", name)
    # pg_trgm stays: 0024 uses it too
