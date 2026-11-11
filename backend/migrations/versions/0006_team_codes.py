"""The agreed teams, each with a short code and the other names people use for it.

SOC (SOC), Forensics (FOR), Incident Response (IR), Incident Management (IM), Intelligence (CTI),
Threat Hunting (CTH) and Leadership (SLT). DFIR becomes Forensics. Identity and Infrastructure merge
into Incident Response, Legal and Comms into Incident Management: their people, tasks, playbook
steps, invitations, mentions and incident involvement move across, and the emptied teams go. The
Threat hunt playbook's SOC steps move to Threat Hunting.

Downgrading puts the old team names back and moves Incident Response's work to Identity and
Incident Management's to Legal; it can't know which of them came from Infrastructure or Comms.

Revision ID: 0006
Revises: 0005
Created: 2026-11-10
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (name, code, other names, description)
TEAMS = [
    ("SOC", "SOC", [], "Security operations: monitoring, triage and first response"),
    ("Forensics", "FOR", ["DFIR"], "Digital forensics: evidence, collection and analysis"),
    (
        "Incident Response",
        "IR",
        [],
        "Containment, eradication and recovery across accounts, endpoints and servers",
    ),
    (
        "Incident Management",
        "IM",
        [],
        "Running the incident: coordination, legal, regulators and communications",
    ),
    ("Intelligence", "CTI", ["Int.", "Intel.", "Threat Intelligence"], "Cyber threat intelligence"),
    ("Threat Hunting", "CTH", ["Hunting"], "Proactive, hypothesis-led hunting"),
    ("Leadership", "SLT", ["Senior Leadership"], "Executives with read-only briefing access"),
]
RENAMED = {"DFIR": "Forensics"}
MERGED = {
    "Identity": "Incident Response",
    "Infrastructure": "Incident Response",
    "Legal": "Incident Management",
    "Comms": "Incident Management",
}
OLD = {
    "DFIR": "Digital forensics and incident response",
    "Identity": "Accounts, credentials and access",
    "Infrastructure": "Endpoints, servers and network",
    "Legal": "Regulatory notification and legal advice",
    "Comms": "Internal and external messaging",
    "Leadership": "Executives with read-only briefing access",
}


def _id(connection: sa.Connection, name: str) -> uuid.UUID | None:
    return connection.execute(sa.text("SELECT id FROM teams WHERE name = :n"), {"n": name}).scalar()


def _move(connection: sa.Connection, old: uuid.UUID, new: uuid.UUID) -> None:
    """Everything that belongs to team `old` now belongs to `new`; then `old` goes."""
    for table in ("users", "invitations", "tasks", "playbook_steps", "mentions"):
        # The table names are this module's own, never input
        moved = f"UPDATE {table} SET team_id = :new WHERE team_id = :old"  # noqa: S608
        connection.execute(sa.text(moved), {"old": old, "new": new})
    connection.execute(
        sa.text(
            "INSERT INTO incident_teams (incident_id, team_id, added_at) "
            "SELECT incident_id, :new, added_at FROM incident_teams WHERE team_id = :old "
            "ON CONFLICT DO NOTHING"
        ),
        {"old": old, "new": new},
    )
    connection.execute(sa.text("DELETE FROM incident_teams WHERE team_id = :old"), {"old": old})
    connection.execute(sa.text("DELETE FROM teams WHERE id = :old"), {"old": old})


def upgrade() -> None:
    op.add_column("teams", sa.Column("code", sa.String(length=8), nullable=True))
    op.add_column(
        "teams",
        sa.Column("aliases", sa.ARRAY(sa.String(length=40)), server_default=sa.text("'{}'"), nullable=False),
    )
    op.create_unique_constraint("uq_teams_code", "teams", ["code"])

    connection = op.get_bind()
    for old, new in RENAMED.items():
        if _id(connection, old) and not _id(connection, new):
            connection.execute(
                sa.text("UPDATE teams SET name = :new WHERE name = :old"), {"old": old, "new": new}
            )
    for name, code, aliases, description in TEAMS:
        if _id(connection, name):
            connection.execute(
                sa.text("UPDATE teams SET code = :code, aliases = :aliases WHERE name = :name"),
                {"name": name, "code": code, "aliases": aliases},
            )
        else:
            connection.execute(
                sa.text(
                    "INSERT INTO teams (id, name, description, code, aliases, teams_webhook, created_at) "
                    "VALUES (:id, :name, :description, :code, :aliases, '', now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "name": name,
                    "description": description,
                    "code": code,
                    "aliases": aliases,
                },
            )
    for old, new in MERGED.items():
        old_id, new_id = _id(connection, old), _id(connection, new)
        if old_id:
            _move(connection, old_id, new_id)

    # The hunt playbook's searching belongs to the hunters now there's a team for them
    connection.execute(
        sa.text(
            "UPDATE playbook_steps SET team_id = :hunting WHERE team_id = :soc AND playbook_id IN "
            "(SELECT id FROM playbooks WHERE applies_to = 'hunt')"
        ),
        {"hunting": _id(connection, "Threat Hunting"), "soc": _id(connection, "SOC")},
    )


def downgrade() -> None:
    connection = op.get_bind()
    for old, description in OLD.items():
        if not _id(connection, old) and old not in RENAMED:
            connection.execute(
                sa.text(
                    "INSERT INTO teams (id, name, description, code, aliases, teams_webhook, created_at) "
                    "VALUES (:id, :name, :description, NULL, '{}', '', now())"
                ),
                {"id": uuid.uuid4(), "name": old, "description": description},
            )
    soc = _id(connection, "SOC")
    for new, old in (("Incident Response", "Identity"), ("Incident Management", "Legal")):
        if _id(connection, new):
            _move(connection, _id(connection, new), _id(connection, old))
    for gone in ("Intelligence", "Threat Hunting"):
        if _id(connection, gone):
            _move(connection, _id(connection, gone), soc)
    for old, new in RENAMED.items():
        connection.execute(
            sa.text("UPDATE teams SET name = :old WHERE name = :new"), {"old": old, "new": new}
        )
    op.drop_constraint("uq_teams_code", "teams", type_="unique")
    op.drop_column("teams", "aliases")
    op.drop_column("teams", "code")
