"""Playbooks become workflows: steps wait for earlier steps, open at a phase, or follow a decision's
outcome; applied playbooks keep each step's state; tasks can be decisions.

The seed playbooks nobody has edited (still at version 1) are rewritten as workflows. Their titles,
due times and checklists are 0005's; this revision adds the order, the phases, the decisions and a
few hand-offs (Intelligence on ransomware, write-ups after a hunt).

Revision ID: 0007
Revises: 0006
Created: 2026-11-10
"""

import importlib.util
import json
import uuid
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _seeds_0005() -> dict[str, dict[str, tuple]]:
    """0005's steps by playbook and title: (due minutes, checklist)."""
    path = Path(__file__).with_name("0005_hunts_and_links.py")
    spec = importlib.util.spec_from_file_location("seeds_0005", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {name: {t: (due, items) for t, _, due, items in steps} for name, *_, steps in module.SEEDS}


def _o(label: str, action: str = "none", opens: list[int] | None = None, phase: str | None = None) -> dict:
    return {"label": label, "action": action, "phase": phase, "opens": opens or []}


# playbook: [(title, team, after, phase, outcomes, new step's (due, checklist) or None for 0005's)]
WORKFLOWS: dict[str, list[tuple]] = {
    "Incident response": [
        ("Confirm and scope the first report", None, [], None, [], None),
        ("Assign roles and open the response channel", None, [], None, [], None),
        ("Preserve evidence before it changes", "Forensics", [0], None, [], None),
        ("First assessment to leadership", None, [0, 1], None, [], None),
        ("Contain", "SOC", [], "contain", [], None),
        ("Eradicate", "Incident Response", [4], "eradicate", [], None),
        ("Recover", "Incident Response", [5], "recover", [], None),
        ("Lessons learned", None, [6], "learn", [], None),
    ],
    "Phishing and BEC": [
        ("Pull the reported message and its headers", "SOC", [], None, [], None),
        ("Find every recipient", "SOC", [0], None, [], None),
        ("Purge the message from mailboxes", "SOC", [1], None, [], None),
        ("Check for credential entry and sign-ins", "Incident Response", [1], None, [], None),
        ("Inspect mailbox rules, forwarding and app consents", "Incident Response", [3], None, [], None),
        ("Block the sender and infrastructure", "SOC", [0], None, [], None),
        ("Check for payment or data requests", None, [0], None, [], None),
    ],
    "Ransomware": [
        ("Isolate affected hosts", "SOC", [], None, [], None),
        ("Preserve memory and disk", "Forensics", [0], None, [], None),
        ("Identify patient zero and the strain", "Forensics", [1], None, [], None),
        ("Check backups are intact and offline", "Incident Response", [], None, [], None),
        ("Scope encryption and exfiltration", "Forensics", [2], None, [], None),
        ("Start the regulator and insurer clocks", "Incident Management", [], None, [], None),
        (
            "Agree the recovery plan with Leadership",
            "Incident Management",
            [3, 4],
            None,
            [],
            (
                1440,
                [
                    "Rebuild or restore, system by system",
                    "Position on payment, with Legal and the insurer",
                    "Order in which services come back",
                ],
            ),
        ),
        ("Staff and customer communications", "Incident Management", [5], None, [], None),
        (
            "Profile the group and watch its leak site",
            "Intelligence",
            [2],
            None,
            [],
            (
                480,
                [
                    "Match the note, extension and leak site to a group",
                    "Known tooling and dwell time",
                    "Watch the leak site for our name",
                ],
            ),
        ),
    ],
    "Account compromise": [
        ("Disable or reset the account", "Incident Response", [], None, [], None),
        ("Review sign-ins and MFA changes", "Incident Response", [0], None, [], None),
        ("Look for persistence", "Incident Response", [1], None, [], None),
        ("Check what the account touched", "SOC", [1], None, [], None),
        ("Search for the same infrastructure elsewhere", "SOC", [3], None, [], None),
    ],
    "Data exposure": [
        ("Confirm what data, and whose", "Forensics", [], None, [], None),
        ("Contain the exposure", "Incident Response", [], None, [], None),
        ("Preserve evidence", "Forensics", [], None, [], None),
        ("Assess the risk to the people affected", "Incident Management", [0], None, [], None),
        (
            "Report to the ICO if required",
            "Incident Management",
            [3],
            None,
            [_o("Reportable: report within 72 hours", opens=[5]), _o("Not reportable: record why")],
            None,
        ),
        ("Tell the people affected", "Incident Management", [], None, [], None),
    ],
    "Threat hunt": [
        ("Write the hypothesis", None, [], None, [], None),
        ("Choose data sources and time range", "Threat Hunting", [0], "collect", [], None),
        ("Run the searches (Shikari)", "Threat Hunting", [1], None, [], None),
        ("Analyse the results", None, [2], "analyse", [], None),
        (
            "Conclude",
            None,
            [3],
            None,
            [
                _o("Confirmed: promote to an incident", "promote", [5, 6]),
                _o("Refuted: close the hunt", "close", [5]),
                _o("Inconclusive: refine the hypothesis", "phase", [0], "hypothesis"),
            ],
            None,
        ),
        (
            "Write up the detection for Shikari's library",
            "Threat Hunting",
            [],
            None,
            [],
            (2880, ["Query and its false positives", "Tested over 30 days", "Added to the library"]),
        ),
        (
            "Share what was found with Intelligence",
            "Intelligence",
            [],
            None,
            [],
            (1440, ["Indicators and infrastructure", "Techniques, with ATT&CK IDs"]),
        ),
    ],
}


def upgrade() -> None:
    op.add_column(
        "playbook_steps",
        sa.Column("after", postgresql.ARRAY(sa.Integer()), server_default="{}", nullable=False),
    )
    op.add_column("playbook_steps", sa.Column("phase", sa.String(length=16), nullable=True))
    op.add_column(
        "playbook_steps", sa.Column("outcomes", postgresql.JSONB(), server_default="[]", nullable=False)
    )
    op.add_column(
        "incident_playbooks", sa.Column("steps", postgresql.JSONB(), server_default="[]", nullable=False)
    )
    op.add_column("tasks", sa.Column("run_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("step", sa.Integer(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("outcomes", postgresql.ARRAY(sa.String(length=120)), server_default="{}", nullable=False),
    )
    op.add_column("tasks", sa.Column("outcome", sa.String(length=120), nullable=True))
    op.create_foreign_key(
        "tasks_run_id_fkey", "tasks", "incident_playbooks", ["run_id"], ["id"], ondelete="SET NULL"
    )

    _rewrite()


def _rewrite() -> None:
    """The unedited seed playbooks, as workflows."""
    connection = op.get_bind()
    teams = dict(connection.execute(sa.text("SELECT name, id FROM teams")).all())
    old = _seeds_0005()
    for name, steps in WORKFLOWS.items():
        playbook = connection.execute(
            sa.text("SELECT id FROM playbooks WHERE name = :name AND version = 1"), {"name": name}
        ).scalar()
        if playbook is None:
            continue
        connection.execute(sa.text("DELETE FROM playbook_steps WHERE playbook_id = :p"), {"p": playbook})
        for position, (title, team, after, phase, outcomes, new) in enumerate(steps):
            due, checklist = new or old[name].get(title) or old[name][title.replace(" with Leadership", "")]
            connection.execute(
                sa.text(
                    "INSERT INTO playbook_steps (id, playbook_id, position, title, description, team_id, "
                    "due_minutes, checklist, after, phase, outcomes) "
                    "VALUES (:id, :playbook, :position, :title, '', :team, :due, :checklist, :after, :phase, "
                    "CAST(:outcomes AS jsonb))"
                ),
                {
                    "id": uuid.uuid4(),
                    "playbook": playbook,
                    "position": position,
                    "title": title,
                    "team": teams.get(team) if team else None,
                    "due": due,
                    "checklist": checklist,
                    "after": after,
                    "phase": phase,
                    "outcomes": json.dumps(outcomes),
                },
            )


def downgrade() -> None:
    op.drop_constraint("tasks_run_id_fkey", "tasks", type_="foreignkey")
    for column in ("outcome", "outcomes", "step", "run_id"):
        op.drop_column("tasks", column)
    op.drop_column("incident_playbooks", "steps")
    for column in ("outcomes", "phase", "after"):
        op.drop_column("playbook_steps", column)
