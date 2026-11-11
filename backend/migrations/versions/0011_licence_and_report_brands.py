"""The installation's licence (free, Pro or MSSP) and report brands: colours and up to three logos,
one brand the default, and the brand an incident's exports use.

Revision ID: 0011
Revises: 0010
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "licences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("installed_by", sa.Uuid(), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["installed_by"], ["users.id"], name=op.f("fk_licences_installed_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_licences")),
    )
    op.create_table(
        "report_brands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("primary", sa.String(length=7), nullable=False),
        sa.Column("secondary", sa.String(length=7), nullable=False),
        sa.Column("prepared_for", sa.String(length=160), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("cover_logo", sa.LargeBinary(), nullable=True),
        sa.Column("header_logo", sa.LargeBinary(), nullable=True),
        sa.Column("client_logo", sa.LargeBinary(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_report_brands_created_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_brands")),
        sa.UniqueConstraint("name", name=op.f("uq_report_brands_name")),
    )
    op.create_index(
        "uq_report_brands_one_default",
        "report_brands",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.add_column("incidents", sa.Column("brand_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_incidents_brand_id_report_brands"),
        "incidents",
        "report_brands",
        ["brand_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_incidents_brand_id_report_brands"), "incidents", type_="foreignkey")
    op.drop_column("incidents", "brand_id")
    op.drop_index("uq_report_brands_one_default", table_name="report_brands")
    op.drop_table("report_brands")
    op.drop_table("licences")
