"""Assessments in structured parts (basis, PHIA probability, negative evidence), Analysis of Competing
Hypotheses (hypotheses, evidence, each analyst's ratings), lessons learned, and the sensitive flag
that limits an incident's report exports to its commander.

Revision ID: 0010
Revises: 0009
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("judgement", sa.Text(), nullable=False),
        sa.Column(
            "probability",
            sa.Enum(
                "remote_chance",
                "highly_unlikely",
                "unlikely",
                "realistic_possibility",
                "likely",
                "highly_likely",
                "almost_certain",
                name="probability",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("so_what", sa.Text(), nullable=False),
        sa.Column("what_next", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("current", "superseded", name="assessment_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_assessments_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_assessments_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["assessments.id"],
            name=op.f("fk_assessments_supersedes_id_assessments"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessments")),
        sa.UniqueConstraint("seq", name=op.f("uq_assessments_seq")),
    )
    op.create_index(op.f("ix_assessments_incident_id"), "assessments", ["incident_id"], unique=False)
    op.create_table(
        "negative_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("absent", sa.String(length=500), nullable=False),
        sa.Column("platform", sa.String(length=120), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=500), nullable=False),
        sa.Column("time_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_to", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["assessments.id"],
            name=op.f("fk_negative_evidence_assessment_id_assessments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_negative_evidence")),
    )
    op.create_index(
        op.f("ix_negative_evidence_assessment_id"), "negative_evidence", ["assessment_id"], unique=False
    )
    op.create_table(
        "ach_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "finding",
                "observable",
                "assumption",
                "absence",
                name="evidence_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("finding_id", sa.Uuid(), nullable=True),
        sa.Column("incident_observable_id", sa.Uuid(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "credibility", sa.Enum("h", "m", "l", name="weight", native_enum=False, length=32), nullable=False
        ),
        sa.Column(
            "relevance", sa.Enum("h", "m", "l", name="weight", native_enum=False, length=32), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_ach_evidence_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_ach_evidence_finding_id_findings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_ach_evidence_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["incident_observable_id"],
            ["incident_observables.id"],
            name=op.f("fk_ach_evidence_incident_observable_id_incident_observables"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ach_evidence")),
        sa.UniqueConstraint("seq", name=op.f("uq_ach_evidence_seq")),
    )
    op.create_index(op.f("ix_ach_evidence_incident_id"), "ach_evidence", ["incident_id"], unique=False)
    op.create_table(
        "ach_hypotheses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "rejected", name="hypothesis_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=False),
        sa.Column("rejected_by", sa.Uuid(), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("milestone", sa.Text(), nullable=False),
        sa.Column("milestone_task_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_ach_hypotheses_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_ach_hypotheses_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["milestone_task_id"],
            ["tasks.id"],
            name=op.f("fk_ach_hypotheses_milestone_task_id_tasks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["rejected_by"],
            ["users.id"],
            name=op.f("fk_ach_hypotheses_rejected_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ach_hypotheses")),
        sa.UniqueConstraint("seq", name=op.f("uq_ach_hypotheses_seq")),
    )
    op.create_index(op.f("ix_ach_hypotheses_incident_id"), "ach_hypotheses", ["incident_id"], unique=False)
    op.create_table(
        "assessment_basis",
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["assessments.id"],
            name=op.f("fk_assessment_basis_assessment_id_assessments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_assessment_basis_finding_id_findings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("assessment_id", "finding_id", name=op.f("pk_assessment_basis")),
    )
    op.create_table(
        "lessons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("went_well", "improve", name="lesson_kind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "area",
            sa.Enum(
                "people",
                "process",
                "technology",
                "detection",
                "communication",
                name="lesson_area",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_lessons_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_lessons_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_lessons_task_id_tasks"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lessons")),
        sa.UniqueConstraint("seq", name=op.f("uq_lessons_seq")),
    )
    op.create_index(op.f("ix_lessons_incident_id"), "lessons", ["incident_id"], unique=False)
    op.create_table(
        "ach_ratings",
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("rater_id", sa.Uuid(), nullable=False),
        sa.Column(
            "value", sa.Enum("c", "i", "na", name="rating", native_enum=False, length=32), nullable=False
        ),
        sa.Column("rated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["ach_evidence.id"],
            name=op.f("fk_ach_ratings_evidence_id_ach_evidence"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"],
            ["ach_hypotheses.id"],
            name=op.f("fk_ach_ratings_hypothesis_id_ach_hypotheses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rater_id"], ["users.id"], name=op.f("fk_ach_ratings_rater_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("hypothesis_id", "evidence_id", "rater_id", name=op.f("pk_ach_ratings")),
    )
    op.add_column(
        "incidents", sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    op.drop_column("incidents", "sensitive")
    op.drop_table("ach_ratings")
    op.drop_index(op.f("ix_lessons_incident_id"), table_name="lessons")
    op.drop_table("lessons")
    op.drop_table("assessment_basis")
    op.drop_index(op.f("ix_ach_hypotheses_incident_id"), table_name="ach_hypotheses")
    op.drop_table("ach_hypotheses")
    op.drop_index(op.f("ix_ach_evidence_incident_id"), table_name="ach_evidence")
    op.drop_table("ach_evidence")
    op.drop_index(op.f("ix_negative_evidence_assessment_id"), table_name="negative_evidence")
    op.drop_table("negative_evidence")
    op.drop_index(op.f("ix_assessments_incident_id"), table_name="assessments")
    op.drop_table("assessments")
