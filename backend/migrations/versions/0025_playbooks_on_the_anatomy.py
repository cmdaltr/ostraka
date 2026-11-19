"""Playbooks are drawn on the attack's anatomy: a playbook is checks, each in a part of the attack (a way
in, the toolkit, an action, a service) or in none ("any"), and a part may carry an "only if". Steps'
decisions, waits, phases, due times and descriptions go, as do the workflow's states on applied
playbooks; an applied playbook records the parts it has taken.

The seed playbooks nobody has edited (still at version 1) are rewritten on the anatomy (`SEEDS`). Any
other playbook keeps its steps as checks not tied to a step, each checklist item a check of its own
after its step, so nothing written is lost. Going back, checks become steps again (without their parts,
conditions or the parts an applied playbook took).

Revision ID: 0025
Revises: 0024
Created: 2026-11-18
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CREDS = "someone entered credentials"
IR = "Incident Response"
CLEAR = sa.text("DELETE FROM playbook_checks WHERE playbook_id = :id")

# (name, applies to, trigger tags, description, {part: only if}, [(part, check, team)])
SEEDS: list[tuple] = [
    (
        "Incident response",
        "incident",
        [],
        "The first hour of any incident, then the work of each later phase.",
        {},
        [
            ("toolkit", "Find what ran on the first hosts: EDR process trees and new services", "Forensics"),
            ("any", "Confirm and scope the first report", None),
            ("any", "Assign roles and open the response channel", None),
            ("any", "Preserve evidence before it changes", "Forensics"),
            ("any", "First assessment to leadership", None),
            ("any", "Contain", "SOC"),
            ("any", "Eradicate", IR),
            ("any", "Recover", IR),
            ("any", "Lessons learned", None),
        ],
    ),
    (
        "Phishing and BEC",
        "incident",
        ["phishing", "bec"],
        "A reported lure, from the message to what the account did after the click.",
        {"credentials": CREDS, "keeps_access": CREDS, "gathers": CREDS},
        [
            ("phishing", "Pull the reported message and its headers", "SOC"),
            ("phishing", "Find every recipient", "SOC"),
            ("phishing", "Purge the message from mailboxes", "SOC"),
            ("phishing", "Check for credential entry and sign-ins", IR),
            ("credentials", "Reset the password and revoke sessions and refresh tokens", IR),
            ("credentials", "Review sign-ins for new locations, devices and token replay", "SOC"),
            ("keeps_access", "List MFA methods and devices registered since the click", IR),
            ("hides", "Inspect mailbox rules, forwarding and app consents", IR),
            ("gathers", "Check MailItemsAccessed for what was read", "SOC"),
            ("extorts", "Check for payment or data requests", None),
            ("infrastructure", "Block the sender and infrastructure", "SOC"),
        ],
    ),
    (
        "Ransomware",
        "incident",
        ["ransomware"],
        "Encryption or a ransom note: contain, find the way in, and recover from clean backups.",
        {},
        [
            ("exploit", "Check edge devices (VPN, firewall, remote access) for known CVEs", IR),
            ("credentials", "Look for VPN and RDP sign-ins before the first host", "SOC"),
            ("toolkit", "Identify patient zero and the strain", "Forensics"),
            ("steals_logins", "Look for credential dumping: LSASS access, NTDS.dit, DCSync", "Forensics"),
            ("moves", "Map movement between hosts: RDP, PsExec, WMI and SMB", "Forensics"),
            ("admin", "Find the accounts used with admin rights, and reset them", IR),
            ("hides", "Check for security tools turned off or uninstalled", "SOC"),
            (
                "takes_out",
                "Look for staging and uploads before encryption: rclone, MEGA, WinSCP",
                "Forensics",
            ),
            ("destroys", "Isolate affected hosts", "SOC"),
            ("destroys", "Check backups are intact and offline", IR),
            ("destroys", "Scope encryption and exfiltration", "Forensics"),
            ("extorts", "Profile the group and watch its leak site", "Intelligence"),
            ("any", "Preserve memory and disk", "Forensics"),
            ("any", "Start the regulator and insurer clocks", "Incident Management"),
            ("any", "Agree the recovery plan with Leadership", "Incident Management"),
            ("any", "Staff and customer communications", "Incident Management"),
        ],
    ),
    (
        "Account compromise",
        "incident",
        ["account", "identity"],
        "An account used by someone else: shut it off, then find what it did and kept.",
        {"admin": "the account has admin rights"},
        [
            ("credentials", "Disable or reset the account", IR),
            ("credentials", "Review sign-ins and MFA changes", IR),
            ("admin", "Review admin actions: role changes, new accounts, policy changes", IR),
            ("keeps_access", "Look for persistence", IR),
            ("gathers", "Check what the account touched", "SOC"),
            ("infrastructure", "Search for the same infrastructure elsewhere", "SOC"),
        ],
    ),
    (
        "Data exposure",
        "incident",
        ["data", "gdpr", "exposure"],
        "Personal or confidential data where it shouldn't be: what, whose, who saw it, and who to tell.",
        {},
        [
            ("takes_out", "Check the access logs for who opened or downloaded it", "Forensics"),
            ("storage", "Contain the exposure", IR),
            ("any", "Confirm what data, and whose", "Forensics"),
            ("any", "Preserve evidence", "Forensics"),
            ("any", "Assess the risk to the people affected", "Incident Management"),
            ("any", "Report to the ICO if required, or record why not", "Incident Management"),
            ("any", "Tell the people affected, if the risk to them is high", "Incident Management"),
        ],
    ),
    (
        "Threat hunt",
        "hunt",
        [],
        "A hypothesis tested against the data, and what the hunt leaves behind.",
        {},
        [
            ("any", "Write the hypothesis", None),
            ("any", "Choose data sources and time range", "Threat Hunting"),
            ("any", "Run the searches (Shikari)", "Threat Hunting"),
            ("any", "Analyse the results", None),
            ("any", "Conclude: promote it, close it, or refine the hypothesis", None),
            ("any", "Write up the detection for Shikari's library", "Threat Hunting"),
            ("any", "Share what was found with Intelligence", "Intelligence"),
        ],
    ),
]


def _insert(connection, playbook_id, rows) -> None:
    """Checks for a playbook, in order: (part, title, team id)."""
    for position, (part, title, team_id) in enumerate(rows):
        connection.execute(
            sa.text(
                "INSERT INTO playbook_checks (id, playbook_id, position, part, title, team_id) "
                "VALUES (:id, :playbook, :position, :part, :title, :team)"
            ),
            {
                "id": uuid.uuid4(),
                "playbook": playbook_id,
                "position": position,
                "part": part,
                "title": title,
                "team": team_id,
            },
        )


def _seed() -> None:
    """The seed playbooks on the anatomy: rewrite those nobody has edited, add any that are missing."""
    connection = op.get_bind()
    teams = dict(connection.execute(sa.text("SELECT name, id FROM teams")).all())
    for name, applies_to, tags, description, conditions, checks in SEEDS:
        row = connection.execute(
            sa.text("SELECT id, version FROM playbooks WHERE name = :name"), {"name": name}
        ).first()
        if row is not None and row.version != 1:  # someone edited it; theirs stays
            continue
        if row is None:
            playbook_id = uuid.uuid4()
            connection.execute(
                sa.text(
                    "INSERT INTO playbooks (id, name, description, trigger_tags, applies_to, is_active, "
                    "conditions, created_at, updated_at, version) VALUES (:id, :name, :description, :tags, "
                    ":applies_to, true, CAST(:conditions AS jsonb), now(), now(), 1)"
                ),
                {
                    "id": playbook_id,
                    "name": name,
                    "description": description,
                    "tags": tags,
                    "applies_to": applies_to,
                    "conditions": json.dumps(conditions),
                },
            )
        else:
            playbook_id = row.id
            connection.execute(
                sa.text(
                    "UPDATE playbooks SET description = :description, "
                    "conditions = CAST(:conditions AS jsonb) WHERE id = :id"
                ),
                {"id": playbook_id, "description": description, "conditions": json.dumps(conditions)},
            )
            connection.execute(CLEAR, {"id": playbook_id})
        _insert(connection, playbook_id, [(part, title, teams.get(team)) for part, title, team in checks])


def _keep_checklists(seeded: set[str]) -> None:
    """Every other playbook: each step's checklist items become checks of their own, after it."""
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT p.id, p.name, s.title, s.team_id, s.checklist FROM playbook_checks s "
            "JOIN playbooks p ON p.id = s.playbook_id ORDER BY p.id, s.position"
        )
    ).all()
    by_playbook: dict = {}
    for playbook_id, name, title, team_id, checklist in rows:
        if name in seeded:
            continue
        by_playbook.setdefault(playbook_id, []).append((title, team_id, checklist or []))
    for playbook_id, steps in by_playbook.items():
        if not any(checklist for _, _, checklist in steps):
            continue
        connection.execute(CLEAR, {"id": playbook_id})
        _insert(
            connection,
            playbook_id,
            [
                ("any", line[:200], team_id)
                for title, team_id, checklist in steps
                for line in [title, *(item for item in checklist if len(item.strip()) >= 3)]
            ],
        )


def upgrade() -> None:
    op.rename_table("playbook_steps", "playbook_checks")
    op.execute("ALTER TABLE playbook_checks RENAME CONSTRAINT pk_playbook_steps TO pk_playbook_checks")
    op.execute(
        "ALTER TABLE playbook_checks RENAME CONSTRAINT fk_playbook_steps_playbook_id_playbooks "
        "TO fk_playbook_checks_playbook_id_playbooks"
    )
    op.execute(
        "ALTER TABLE playbook_checks RENAME CONSTRAINT fk_playbook_steps_team_id_teams "
        "TO fk_playbook_checks_team_id_teams"
    )
    op.execute("ALTER INDEX ix_playbook_steps_playbook_id RENAME TO ix_playbook_checks_playbook_id")
    op.add_column(
        "playbook_checks", sa.Column("part", sa.String(length=32), server_default="any", nullable=False)
    )
    op.add_column(
        "playbooks", sa.Column("conditions", postgresql.JSONB(), server_default="{}", nullable=False)
    )
    seeded = {
        name
        for (name,) in op.get_bind().execute(
            sa.text("SELECT name FROM playbooks WHERE version = 1 AND name = ANY(:names)"),
            {"names": [s[0] for s in SEEDS]},
        )
    }
    _keep_checklists(seeded)
    for column in ("description", "due_minutes", "checklist", "after", "phase", "outcomes"):
        op.drop_column("playbook_checks", column)
    _seed()

    op.add_column(
        "incident_playbooks",
        sa.Column("parts", postgresql.ARRAY(sa.String(length=32)), server_default="{}", nullable=False),
    )
    # A playbook applied before today took everything it had then
    op.execute("UPDATE incident_playbooks SET parts = ARRAY['any']::varchar(32)[]")
    op.drop_column("incident_playbooks", "steps")
    for column in ("outcomes", "outcome", "step"):
        op.drop_column("tasks", column)


def downgrade() -> None:
    op.add_column("tasks", sa.Column("step", sa.Integer(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("outcomes", postgresql.ARRAY(sa.String(length=120)), server_default="{}", nullable=False),
    )
    op.add_column("tasks", sa.Column("outcome", sa.String(length=120), nullable=True))
    op.add_column(
        "incident_playbooks", sa.Column("steps", postgresql.JSONB(), server_default="[]", nullable=False)
    )
    op.drop_column("incident_playbooks", "parts")

    op.add_column("playbook_checks", sa.Column("description", sa.Text(), server_default="", nullable=False))
    op.add_column("playbook_checks", sa.Column("due_minutes", sa.Integer(), nullable=True))
    op.add_column(
        "playbook_checks",
        sa.Column("checklist", postgresql.ARRAY(sa.String(length=300)), server_default="{}", nullable=False),
    )
    op.add_column(
        "playbook_checks",
        sa.Column("after", postgresql.ARRAY(sa.Integer()), server_default="{}", nullable=False),
    )
    op.add_column("playbook_checks", sa.Column("phase", sa.String(length=16), nullable=True))
    op.add_column(
        "playbook_checks", sa.Column("outcomes", postgresql.JSONB(), server_default="[]", nullable=False)
    )
    op.drop_column("playbook_checks", "part")
    op.drop_column("playbooks", "conditions")
    op.execute("ALTER INDEX ix_playbook_checks_playbook_id RENAME TO ix_playbook_steps_playbook_id")
    op.execute(
        "ALTER TABLE playbook_checks RENAME CONSTRAINT fk_playbook_checks_team_id_teams "
        "TO fk_playbook_steps_team_id_teams"
    )
    op.execute(
        "ALTER TABLE playbook_checks RENAME CONSTRAINT fk_playbook_checks_playbook_id_playbooks "
        "TO fk_playbook_steps_playbook_id_playbooks"
    )
    op.execute("ALTER TABLE playbook_checks RENAME CONSTRAINT pk_playbook_checks TO pk_playbook_steps")
    op.rename_table("playbook_checks", "playbook_steps")
