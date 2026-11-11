"""The graph and chain of custody: where an asset connection was seen, evidence items (exhibits),
their append-only custody entries, and findings citing exhibits.

The database backs up the append-only rule: a custody entry can't be updated, and can only be
deleted by the cascade when its incident is removed, never directly. The same goes for an exhibit,
whose hashes can't be changed once written.

Revision ID: 0016
Revises: 0013
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("received", "offered", "accepted", "declined", "withdrawn")

GUARDS = (
    """
CREATE FUNCTION custody_entries_append_only() RETURNS trigger AS $$
BEGIN
    -- A direct DELETE runs at depth 1; the cascade from a removed exhibit runs inside its RI trigger
    IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'custody entries are append-only';
END;
$$ LANGUAGE plpgsql""",
    """
CREATE TRIGGER custody_entries_append_only
    BEFORE UPDATE OR DELETE ON custody_entries
    FOR EACH ROW EXECUTE FUNCTION custody_entries_append_only()""",
    """
CREATE FUNCTION evidence_items_fixed_hashes() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF pg_trigger_depth() > 1 THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'an exhibit is removed only with its incident';
    END IF;
    IF NEW.sha256 IS DISTINCT FROM OLD.sha256 OR NEW.sha1 IS DISTINCT FROM OLD.sha1
       OR NEW.md5 IS DISTINCT FROM OLD.md5 OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
       OR NEW.created_at IS DISTINCT FROM OLD.created_at OR NEW.incident_id IS DISTINCT FROM OLD.incident_id
    THEN
        RAISE EXCEPTION 'an exhibit''s hashes are fixed once recorded';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql""",
    """
CREATE TRIGGER evidence_items_fixed_hashes
    BEFORE UPDATE OR DELETE ON evidence_items
    FOR EACH ROW EXECUTE FUNCTION evidence_items_fixed_hashes()""",
)


def upgrade() -> None:
    op.add_column(
        "asset_links", sa.Column("source", sa.String(length=255), nullable=False, server_default="")
    )
    op.alter_column("asset_links", "source", server_default=None)

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("sha1", sa.String(length=40), nullable=False),
        sa.Column("md5", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("location", sa.String(length=500), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_evidence_items_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_evidence_items_created_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_items")),
        sa.UniqueConstraint("seq", name=op.f("uq_evidence_items_seq")),
    )
    op.create_index(op.f("ix_evidence_items_incident_id"), "evidence_items", ["incident_id"], unique=False)

    op.create_table(
        "custody_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Enum(*KINDS, name="custody_kind", native_enum=False, length=32), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_name", sa.String(length=120), nullable=False),
        sa.Column("from_id", sa.Uuid(), nullable=True),
        sa.Column("from_name", sa.String(length=120), nullable=False),
        sa.Column("to_id", sa.Uuid(), nullable=True),
        sa.Column("to_name", sa.String(length=120), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=False),
        sa.Column("acknowledgement", sa.Text(), nullable=False),
        sa.Column(
            "hash_check",
            sa.Enum("match", "mismatch", name="hash_check", native_enum=False, length=32),
            nullable=True,
        ),
        sa.Column("offer_position", sa.Integer(), nullable=True),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_items.id"],
            name=op.f("fk_custody_entries_evidence_id_evidence_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custody_entries")),
        sa.UniqueConstraint("evidence_id", "position", name="uq_custody_entries_position"),
    )
    op.create_index(op.f("ix_custody_entries_evidence_id"), "custody_entries", ["evidence_id"], unique=False)

    op.create_table(
        "finding_evidence",
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("cited_by", sa.Uuid(), nullable=True),
        sa.Column("cited_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_finding_evidence_finding_id_findings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_items.id"],
            name=op.f("fk_finding_evidence_evidence_id_evidence_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cited_by"], ["users.id"], name=op.f("fk_finding_evidence_cited_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("finding_id", "evidence_id", name=op.f("pk_finding_evidence")),
    )
    op.create_index(
        op.f("ix_finding_evidence_evidence_id"), "finding_evidence", ["evidence_id"], unique=False
    )

    for statement in GUARDS:  # one statement per call: asyncpg runs them prepared
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER evidence_items_fixed_hashes ON evidence_items")
    op.execute("DROP FUNCTION evidence_items_fixed_hashes()")
    op.execute("DROP TRIGGER custody_entries_append_only ON custody_entries")
    op.execute("DROP FUNCTION custody_entries_append_only()")
    op.drop_index(op.f("ix_finding_evidence_evidence_id"), table_name="finding_evidence")
    op.drop_table("finding_evidence")
    op.drop_index(op.f("ix_custody_entries_evidence_id"), table_name="custody_entries")
    op.drop_table("custody_entries")
    op.drop_index(op.f("ix_evidence_items_incident_id"), table_name="evidence_items")
    op.drop_table("evidence_items")
    op.drop_column("asset_links", "source")
