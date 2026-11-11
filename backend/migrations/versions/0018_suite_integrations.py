"""The suite links: Rowanholt and Shikari servers, incidents linked to Rowanholt jobs and cases,
findings citing Rowanholt rows, and detection gaps.

Revision ID: 0018
Revises: 0013
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _who(column: str, table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=op.f(f"fk_{table}_{column}_users"), ondelete="SET NULL"
    )


def upgrade() -> None:
    op.create_table(
        "integrations",
        sa.Column(
            "product",
            sa.Enum("rowanholt", "shikari", name="integration_product", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("webhook_secret", sa.Text(), nullable=False),
        sa.Column("detection_team_id", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_ok", sa.Boolean(), nullable=True),
        sa.Column("check_message", sa.String(length=300), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["detection_team_id"],
            ["teams.id"],
            name=op.f("fk_integrations_detection_team_id_teams"),
            ondelete="SET NULL",
        ),
        _who("updated_by", "integrations"),
        sa.PrimaryKeyConstraint("product", name=op.f("pk_integrations")),
    )
    op.create_table(
        "rowanholt_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("case_number", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("jobs", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_by", sa.Uuid(), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_rowanholt_links_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        _who("linked_by", "rowanholt_links"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rowanholt_links")),
        sa.UniqueConstraint("incident_id", "job_id", "case_number", name="uq_rowanholt_links_target"),
    )
    op.create_index(op.f("ix_rowanholt_links_incident_id"), "rowanholt_links", ["incident_id"])
    op.create_table(
        "rowanholt_citations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("verification_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("case_number", sa.String(length=200), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("artefact", sa.String(length=1000), nullable=False),
        sa.Column("row_ref", sa.String(length=32), nullable=False),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.Column("excerpt", sa.JSON(), nullable=False),
        sa.Column("remote_finding_id", sa.String(length=64), nullable=True),
        sa.Column("remote_status", sa.String(length=16), nullable=False),
        sa.Column("cited_by", sa.Uuid(), nullable=True),
        sa.Column("cited_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_rowanholt_citations_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_rowanholt_citations_finding_id_findings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verification_id"],
            ["verifications.id"],
            name=op.f("fk_rowanholt_citations_verification_id_verifications"),
            ondelete="SET NULL",
        ),
        _who("cited_by", "rowanholt_citations"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rowanholt_citations")),
    )
    op.create_index(op.f("ix_rowanholt_citations_incident_id"), "rowanholt_citations", ["incident_id"])
    op.create_index(op.f("ix_rowanholt_citations_finding_id"), "rowanholt_citations", ["finding_id"])
    op.create_index(
        "uq_rowanholt_citations_remote",
        "rowanholt_citations",
        ["incident_id", "remote_finding_id"],
        unique=True,
        postgresql_where=sa.text("remote_finding_id IS NOT NULL"),
    )
    op.create_table(
        "detection_gaps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("technique", sa.String(length=16), nullable=False),
        sa.Column("technique_name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "closed", name="gap_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("finding_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("opened_by", sa.Uuid(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_error", sa.String(length=300), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_detection_gaps_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_detection_gaps_finding_id_findings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_detection_gaps_task_id_tasks"), ondelete="SET NULL"
        ),
        _who("opened_by", "detection_gaps"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detection_gaps")),
        sa.UniqueConstraint("incident_id", "technique", name="uq_detection_gaps_technique"),
    )
    op.create_index(op.f("ix_detection_gaps_incident_id"), "detection_gaps", ["incident_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_detection_gaps_incident_id"), table_name="detection_gaps")
    op.drop_table("detection_gaps")
    op.drop_index("uq_rowanholt_citations_remote", table_name="rowanholt_citations")
    op.drop_index(op.f("ix_rowanholt_citations_finding_id"), table_name="rowanholt_citations")
    op.drop_index(op.f("ix_rowanholt_citations_incident_id"), table_name="rowanholt_citations")
    op.drop_table("rowanholt_citations")
    op.drop_index(op.f("ix_rowanholt_links_incident_id"), table_name="rowanholt_links")
    op.drop_table("rowanholt_links")
    op.drop_table("integrations")
