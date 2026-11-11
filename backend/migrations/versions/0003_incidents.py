"""Incidents: teams and roles, observables and sightings, assets and links, findings and
verifications, comments and mentions, tag definitions, and the hash-chained activity log.

Revision ID: 0003
Revises: 0002
Created: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_counters",
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("year", name=op.f("pk_incident_counters")),
    )
    op.create_table(
        "observables",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "ip_address",
                "cidr",
                "domain",
                "url",
                "email",
                "email_subject",
                "file_hash_md5",
                "file_hash_sha1",
                "file_hash_sha256",
                "file_name",
                "file_path",
                "process_name",
                "process_command",
                "registry_key",
                "service_name",
                "user_account",
                "phone_number",
                "alias",
                "cve",
                "mutex",
                "certificate",
                "bitcoin_address",
                "asn",
                "mac_address",
                "user_agent",
                "mitre_technique",
                "other",
                name="observable_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_observables")),
        sa.UniqueConstraint("kind", "value_key", name="uq_observables_kind_value"),
        sa.UniqueConstraint("seq", name=op.f("uq_observables_seq")),
    )
    op.create_table(
        "tag_definitions",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_tag_definitions")),
    )
    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "severity",
            sa.Enum("critical", "high", "medium", "low", name="severity", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "tlp",
            sa.Enum(
                "red", "amber+strict", "amber", "green", "clear", name="tlp", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "phase",
            sa.Enum(
                "detect",
                "scope",
                "contain",
                "eradicate",
                "recover",
                "learn",
                name="phase",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("open", "closed", name="incident_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("declared_by", sa.Uuid(), nullable=True),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("aware_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["declared_by"], ["users.id"], name=op.f("fk_incidents_declared_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
        sa.UniqueConstraint("year", "number", name="uq_incidents_year_number"),
    )
    op.create_table(
        "activity_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_activity_events_actor_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_activity_events_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity_events")),
        sa.UniqueConstraint("incident_id", "position", name="uq_activity_incident_position"),
    )
    op.create_index(op.f("ix_activity_events_incident_id"), "activity_events", ["incident_id"], unique=False)
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("public_ip", sa.String(length=64), nullable=False),
        sa.Column("os", sa.String(length=120), nullable=False),
        sa.Column("primary_user", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("data_types", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column(
            "criticality",
            sa.Enum(
                "critical", "high", "medium", "low", name="asset_criticality", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "identified",
                "investigating",
                "contained",
                "eradicated",
                "recovered",
                "clean",
                name="asset_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("is_patient_zero", sa.Boolean(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_assets_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_assets_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
        sa.UniqueConstraint("seq", name=op.f("uq_assets_seq")),
    )
    op.create_index(op.f("ix_assets_incident_id"), "assets", ["incident_id"], unique=False)
    op.create_index(
        "uq_assets_one_patient_zero",
        "assets",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text("is_patient_zero"),
    )
    op.create_table(
        "comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(length=24), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("fk_comments_author_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_comments_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["comments.id"], name=op.f("fk_comments_parent_id_comments"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"], ["users.id"], name=op.f("fk_comments_resolved_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
    )
    op.create_index(
        "ix_comments_target", "comments", ["incident_id", "target_type", "target_id"], unique=False
    )
    op.create_table(
        "incident_members",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "commander",
                "comms",
                "scribe",
                "responder",
                name="incident_role",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_members_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_incident_members_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("incident_id", "user_id", name=op.f("pk_incident_members")),
    )
    op.create_index(
        "uq_incident_members_one_commander",
        "incident_members",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text("role = 'commander'"),
    )
    op.create_table(
        "incident_observables",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("observable_id", sa.Uuid(), nullable=False),
        sa.Column(
            "verdict",
            sa.Enum(
                "unknown", "suspicious", "malicious", "benign", name="verdict", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["users.id"],
            name=op.f("fk_incident_observables_added_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_observables_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observable_id"],
            ["observables.id"],
            name=op.f("fk_incident_observables_observable_id_observables"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_observables")),
        sa.UniqueConstraint("incident_id", "observable_id", name="uq_incident_observables_pair"),
    )
    op.create_index(
        op.f("ix_incident_observables_incident_id"), "incident_observables", ["incident_id"], unique=False
    )
    op.create_index(
        op.f("ix_incident_observables_observable_id"), "incident_observables", ["observable_id"], unique=False
    )
    op.create_table(
        "incident_teams",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_teams_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_incident_teams_team_id_teams"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("incident_id", "team_id", name=op.f("pk_incident_teams")),
    )
    op.create_table(
        "asset_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "lateral_movement",
                "c2",
                "exfiltration",
                "other",
                name="link_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("technique", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_asset_links_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_asset_links_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["assets.id"], name=op.f("fk_asset_links_source_id_assets"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_id"], ["assets.id"], name=op.f("fk_asset_links_target_id_assets"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_links")),
    )
    op.create_index(op.f("ix_asset_links_incident_id"), "asset_links", ["incident_id"], unique=False)
    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "evidence", "intelligence", "potential", name="finding_kind", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("lead", "confirmed", "refuted", name="finding_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("techniques", postgresql.ARRAY(sa.String(length=16)), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("source_reliability", sa.String(length=1), nullable=True),
        sa.Column("information_credibility", sa.String(length=1), nullable=True),
        sa.Column("handling_code", sa.String(length=1), nullable=True),
        sa.Column("handling_conditions", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], name=op.f("fk_findings_asset_id_assets"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_findings_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_findings_decided_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_findings_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
        sa.UniqueConstraint("seq", name=op.f("uq_findings_seq")),
    )
    op.create_index(op.f("ix_findings_incident_id"), "findings", ["incident_id"], unique=False)
    op.create_table(
        "mentions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["comment_id"], ["comments.id"], name=op.f("fk_mentions_comment_id_comments"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_mentions_team_id_teams"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_mentions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mentions")),
    )
    op.create_index(op.f("ix_mentions_comment_id"), "mentions", ["comment_id"], unique=False)
    op.create_index(op.f("ix_mentions_team_id"), "mentions", ["team_id"], unique=False)
    op.create_index(op.f("ix_mentions_user_id"), "mentions", ["user_id"], unique=False)
    op.create_table(
        "sightings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_observable_id", sa.Uuid(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("user_account", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], name=op.f("fk_sightings_asset_id_assets"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["incident_observable_id"],
            ["incident_observables.id"],
            name=op.f("fk_sightings_incident_observable_id_incident_observables"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"], ["users.id"], name=op.f("fk_sightings_recorded_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sightings")),
    )
    op.create_index(
        op.f("ix_sightings_incident_observable_id"), "sightings", ["incident_observable_id"], unique=False
    )
    op.create_table(
        "finding_observables",
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("incident_observable_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_finding_observables_finding_id_findings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["incident_observable_id"],
            ["incident_observables.id"],
            name=op.f("fk_finding_observables_incident_observable_id_incident_observables"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("finding_id", "incident_observable_id", name=op.f("pk_finding_observables")),
    )
    op.create_table(
        "verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column(
            "method",
            sa.Enum("search", "rowanholt", "other", name="verification_method", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("platform", sa.String(length=120), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=500), nullable=False),
        sa.Column("time_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("reference", sa.String(length=500), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("ran_by", sa.Uuid(), nullable=True),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_verifications_finding_id_findings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ran_by"], ["users.id"], name=op.f("fk_verifications_ran_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verifications")),
    )
    op.create_index(op.f("ix_verifications_finding_id"), "verifications", ["finding_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_verifications_finding_id"), table_name="verifications")
    op.drop_table("verifications")
    op.drop_table("finding_observables")
    op.drop_index(op.f("ix_sightings_incident_observable_id"), table_name="sightings")
    op.drop_table("sightings")
    op.drop_index(op.f("ix_mentions_user_id"), table_name="mentions")
    op.drop_index(op.f("ix_mentions_team_id"), table_name="mentions")
    op.drop_index(op.f("ix_mentions_comment_id"), table_name="mentions")
    op.drop_table("mentions")
    op.drop_index(op.f("ix_findings_incident_id"), table_name="findings")
    op.drop_table("findings")
    op.drop_index(op.f("ix_asset_links_incident_id"), table_name="asset_links")
    op.drop_table("asset_links")
    op.drop_table("incident_teams")
    op.drop_index(op.f("ix_incident_observables_observable_id"), table_name="incident_observables")
    op.drop_index(op.f("ix_incident_observables_incident_id"), table_name="incident_observables")
    op.drop_table("incident_observables")
    op.drop_index(
        "uq_incident_members_one_commander",
        table_name="incident_members",
        postgresql_where=sa.text("role = 'commander'"),
    )
    op.drop_table("incident_members")
    op.drop_index("ix_comments_target", table_name="comments")
    op.drop_table("comments")
    op.drop_index(
        "uq_assets_one_patient_zero", table_name="assets", postgresql_where=sa.text("is_patient_zero")
    )
    op.drop_index(op.f("ix_assets_incident_id"), table_name="assets")
    op.drop_table("assets")
    op.drop_index(op.f("ix_activity_events_incident_id"), table_name="activity_events")
    op.drop_table("activity_events")
    op.drop_table("incidents")
    op.drop_table("tag_definitions")
    op.drop_table("observables")
    op.drop_table("incident_counters")
