"""Hunts beside incidents, links between records, subtasks, tasks waiting on tasks, and the playbooks
ciphr starts with.

Downgrading deletes every hunt (and the seeded playbooks nobody has edited), since the older schema
can't hold them.

Revision ID: 0005
Revises: 0004
Created: 2026-11-10
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND = sa.Enum("incident", "hunt", name="record_kind", native_enum=False, length=32)

# (name, for, trigger tags, description, [(title, team, due after minutes, checklist)])
# A team of None gives the task to whoever applies the playbook.
SEEDS = [
    (
        "Incident response",
        "incident",
        [],
        "The first hour of any incident, then one task for each later phase.",
        [
            (
                "Confirm and scope the first report",
                None,
                30,
                [
                    "Confirm it with a search on the host or source, not just the alert",
                    "Record when we became aware",
                    "Set severity and TLP",
                    "Name the hosts, accounts and data involved so far",
                ],
            ),
            (
                "Assign roles and open the response channel",
                None,
                15,
                [
                    "Confirm the incident commander",
                    "Name a scribe and a comms lead",
                    "Involve the teams needed",
                    "Agree how often updates go out",
                ],
            ),
            (
                "Preserve evidence before it changes",
                "DFIR",
                60,
                [
                    "Acquire memory from affected hosts (Rowanholt)",
                    "Collect disk images or triage packages",
                    "Export logs before they roll over",
                    "Record chain of custody",
                ],
            ),
            (
                "First assessment to leadership",
                None,
                60,
                [
                    "Bottom line first: what happened, what's affected, what we're doing",
                    "State confidence with the probability yardstick",
                    "Say what isn't known yet",
                    "Set the time of the next update",
                ],
            ),
            (
                "Contain",
                "SOC",
                240,
                [
                    "Isolate affected hosts",
                    "Disable or reset compromised accounts",
                    "Block known malicious infrastructure",
                    "Confirm containment held with a follow-up search",
                ],
            ),
            (
                "Eradicate",
                "Infrastructure",
                None,
                [
                    "Remove persistence",
                    "Close or mitigate the way in",
                    "Rebuild hosts that can't be trusted",
                    "Rotate exposed credentials",
                ],
            ),
            (
                "Recover",
                "Infrastructure",
                None,
                [
                    "Restore services in priority order",
                    "Watch restored systems for recurrence",
                    "Confirm with service owners that they work",
                ],
            ),
            (
                "Lessons learned",
                None,
                None,
                [
                    "Hold the review within two weeks",
                    "Record what worked and what didn't",
                    "Turn each action into a task with an owner",
                    "Send detection gaps to Shikari",
                ],
            ),
        ],
    ),
    (
        "Phishing and BEC",
        "incident",
        ["phishing", "bec"],
        "A reported or detected phish, through to mailbox compromise and payment fraud.",
        [
            (
                "Pull the reported message and its headers",
                "SOC",
                30,
                [
                    "Authentication results: SPF, DKIM, DMARC",
                    "Attachments and links added as observables",
                    "Sending infrastructure",
                ],
            ),
            (
                "Find every recipient",
                "SOC",
                60,
                [
                    "Message trace on sender, subject and URLs",
                    "Who received it, opened it and clicked",
                ],
            ),
            ("Purge the message from mailboxes", "SOC", 60, []),
            (
                "Check for credential entry and sign-ins",
                "Identity",
                60,
                [
                    "Sign-ins from new locations or devices after the click",
                    "MFA prompts approved or methods registered",
                    "Reset and revoke sessions for anyone who entered credentials",
                ],
            ),
            (
                "Inspect mailbox rules, forwarding and app consents",
                "Identity",
                120,
                [
                    "Inbox rules created since the click",
                    "External forwarding",
                    "New OAuth app consents",
                    "Delegates added",
                ],
            ),
            ("Block the sender and infrastructure", "SOC", 120, []),
            (
                "Check for payment or data requests",
                None,
                240,
                [
                    "Ask finance about bank detail changes requested by affected mailboxes",
                    "Warn suppliers and customers the mailbox wrote to",
                ],
            ),
        ],
    ),
    (
        "Ransomware",
        "incident",
        ["ransomware", "extortion"],
        "Encryption or extortion: contain first, preserve evidence, then decide on recovery.",
        [
            (
                "Isolate affected hosts",
                "SOC",
                15,
                [
                    "Network-isolate through EDR rather than powering off",
                    "Disable accounts used to spread",
                    "Cut VPN and remote access for affected users",
                ],
            ),
            (
                "Preserve memory and disk",
                "DFIR",
                60,
                [
                    "Memory first, before any reboot",
                    "Keep the ransom note, encrypted samples and the binary if found",
                ],
            ),
            (
                "Identify patient zero and the strain",
                "DFIR",
                240,
                [
                    "Note and extension checked against public trackers",
                    "Earliest encryption time",
                    "Initial access vector",
                ],
            ),
            (
                "Check backups are intact and offline",
                "Infrastructure",
                60,
                [
                    "Last good backup for each critical system",
                    "Backup admin accounts not compromised",
                    "Backups isolated from the domain",
                ],
            ),
            (
                "Scope encryption and exfiltration",
                "DFIR",
                480,
                [
                    "Which hosts and shares are encrypted",
                    "Large outbound transfers in the days before",
                    "Leak sites watched for the organisation's name",
                ],
            ),
            (
                "Start the regulator and insurer clocks",
                "Legal",
                240,
                [
                    "Personal data affected: UK GDPR 72 hours from awareness",
                    "Notify the cyber insurer as the policy requires",
                    "Report to the NCSC and Action Fraud where appropriate",
                ],
            ),
            (
                "Agree the recovery plan",
                "Leadership",
                1440,
                [
                    "Rebuild or restore, system by system",
                    "Position on payment, with Legal and the insurer",
                    "Order in which services come back",
                ],
            ),
            ("Staff and customer communications", "Comms", 240, []),
        ],
    ),
    (
        "Account compromise",
        "incident",
        ["account-compromise", "credential-theft"],
        "A user or service account in the wrong hands.",
        [
            (
                "Disable or reset the account",
                "Identity",
                15,
                [
                    "Reset the password and revoke sessions and refresh tokens",
                    "Remove MFA methods the attacker added",
                ],
            ),
            (
                "Review sign-ins and MFA changes",
                "Identity",
                60,
                [
                    "Sign-ins by location, device and IP since the first suspicious one",
                    "MFA registrations and approvals",
                ],
            ),
            (
                "Look for persistence",
                "Identity",
                120,
                [
                    "App consents and service principals",
                    "Mailbox rules and forwarding",
                    "Devices registered",
                    "Role assignments changed",
                ],
            ),
            (
                "Check what the account touched",
                "SOC",
                240,
                [
                    "Files accessed or downloaded",
                    "Mail sent",
                    "Admin actions in the audit log",
                ],
            ),
            (
                "Search for the same infrastructure elsewhere",
                "SOC",
                240,
                [
                    "Sign-ins to other accounts from the same IPs or user agents",
                ],
            ),
        ],
    ),
    (
        "Data exposure",
        "incident",
        ["data-exposure", "data-breach"],
        "Personal or sensitive data seen by people who shouldn't have it.",
        [
            (
                "Confirm what data, and whose",
                "DFIR",
                240,
                [
                    "Data types: personal, special category, financial, credentials",
                    "Number of people and records",
                    "Where it went and who could see it",
                ],
            ),
            (
                "Contain the exposure",
                "Infrastructure",
                60,
                [
                    "Remove public access or sharing",
                    "Request takedown if it's been published",
                ],
            ),
            ("Preserve evidence", "DFIR", 120, []),
            (
                "Assess the risk to the people affected",
                "Legal",
                1440,
                [
                    "Likelihood and severity of harm",
                    "Decide whether it must be reported",
                ],
            ),
            (
                "Report to the ICO if required",
                "Legal",
                2880,
                [
                    "Due 48 hours after applying, to leave a margin on the 72-hour clock",
                ],
            ),
            (
                "Tell the people affected",
                "Comms",
                2880,
                [
                    "Wording agreed with Legal",
                    "What they should do now",
                ],
            ),
        ],
    ),
    (
        "Threat hunt",
        "hunt",
        ["hunt"],
        "A hypothesis-led hunt: Shikari runs the searches, ciphr keeps the work and the evidence.",
        [
            (
                "Write the hypothesis",
                None,
                60,
                [
                    "What behaviour, by whom, and why it might be here",
                    "The ATT&CK techniques involved",
                    "What would prove it, and what would disprove it",
                ],
            ),
            (
                "Choose data sources and time range",
                "SOC",
                120,
                [
                    "Logs that can see the behaviour, and how far back they go",
                    "Gaps that would hide it",
                ],
            ),
            (
                "Run the searches (Shikari)",
                "SOC",
                480,
                [
                    "Record each query and platform as a verification",
                    "Keep hits as leads, not findings",
                ],
            ),
            (
                "Analyse the results",
                None,
                1440,
                [
                    "Separate benign explanations from leads",
                    "Confirm or refute each lead with a search on that host",
                ],
            ),
            (
                "Conclude",
                None,
                None,
                [
                    "What was found, and with what confidence",
                    "Promote to an incident if the hypothesis held",
                    "Turn visibility gaps into Shikari detections",
                ],
            ),
        ],
    ),
]


def upgrade() -> None:
    op.create_table(
        "incident_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "parent", "related", "duplicate", "promoted", name="link_type", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_incident_links_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["incidents.id"],
            name=op.f("fk_incident_links_source_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["incidents.id"],
            name=op.f("fk_incident_links_target_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_links")),
        sa.UniqueConstraint("source_id", "target_id", name="uq_incident_links_pair"),
    )
    op.create_index(op.f("ix_incident_links_source_id"), "incident_links", ["source_id"])
    op.create_index(op.f("ix_incident_links_target_id"), "incident_links", ["target_id"])
    op.create_index(
        "uq_incident_links_one_parent",
        "incident_links",
        ["target_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'parent'"),
    )
    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("blocked_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["blocked_by_id"],
            ["tasks.id"],
            name=op.f("fk_task_dependencies_blocked_by_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_task_dependencies_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_task_dependencies_task_id_tasks"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("task_id", "blocked_by_id", name=op.f("pk_task_dependencies")),
    )
    op.create_index(op.f("ix_task_dependencies_blocked_by_id"), "task_dependencies", ["blocked_by_id"])

    # Every existing record is an incident; numbering becomes per kind and year
    op.add_column("incidents", sa.Column("kind", KIND, nullable=False, server_default="incident"))
    op.drop_constraint("uq_incidents_year_number", "incidents", type_="unique")
    op.create_unique_constraint("uq_incidents_kind_year_number", "incidents", ["kind", "year", "number"])
    op.add_column("incident_counters", sa.Column("kind", KIND, nullable=False, server_default="incident"))
    op.drop_constraint("pk_incident_counters", "incident_counters", type_="primary")
    op.create_primary_key("pk_incident_counters", "incident_counters", ["kind", "year"])

    op.add_column("playbooks", sa.Column("applies_to", KIND, nullable=False, server_default="incident"))
    op.add_column("tasks", sa.Column("parent_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_tasks_parent_id"), "tasks", ["parent_id"])
    op.create_foreign_key(
        op.f("fk_tasks_parent_id_tasks"), "tasks", "tasks", ["parent_id"], ["id"], ondelete="CASCADE"
    )

    _seed()


def _seed() -> None:
    connection = op.get_bind()
    teams = dict(connection.execute(sa.text("SELECT name, id FROM teams")).all())
    taken = set(connection.execute(sa.text("SELECT name FROM playbooks")).scalars())
    for name, applies_to, tags, description, steps in SEEDS:
        if name in taken:  # someone already wrote one by that name; theirs stays
            continue
        playbook_id = uuid.uuid4()
        connection.execute(
            sa.text(
                "INSERT INTO playbooks (id, name, description, trigger_tags, applies_to, is_active, "
                "created_at, updated_at, version) "
                "VALUES (:id, :name, :description, :tags, :applies_to, true, now(), now(), 1)"
            ),
            {
                "id": playbook_id,
                "name": name,
                "description": description,
                "tags": tags,
                "applies_to": applies_to,
            },
        )
        for position, (title, team, due, checklist) in enumerate(steps):
            connection.execute(
                sa.text(
                    "INSERT INTO playbook_steps (id, playbook_id, position, title, description, team_id, "
                    "due_minutes, checklist) "
                    "VALUES (:id, :playbook, :position, :title, '', :team, :due, :checklist)"
                ),
                {
                    "id": uuid.uuid4(),
                    "playbook": playbook_id,
                    "position": position,
                    "title": title,
                    "team": teams.get(team) if team else None,
                    "due": due,
                    "checklist": checklist,
                },
            )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM playbooks WHERE created_by IS NULL AND version = 1 AND name = ANY(:names)"),
        {"names": [s[0] for s in SEEDS]},
    )
    connection.execute(sa.text("DELETE FROM incidents WHERE kind = 'hunt'"))
    connection.execute(sa.text("DELETE FROM incident_counters WHERE kind = 'hunt'"))

    op.drop_constraint(op.f("fk_tasks_parent_id_tasks"), "tasks", type_="foreignkey")
    op.drop_index(op.f("ix_tasks_parent_id"), table_name="tasks")
    op.drop_column("tasks", "parent_id")
    op.drop_column("playbooks", "applies_to")
    op.drop_constraint("pk_incident_counters", "incident_counters", type_="primary")
    op.create_primary_key("pk_incident_counters", "incident_counters", ["year"])
    op.drop_column("incident_counters", "kind")
    op.drop_constraint("uq_incidents_kind_year_number", "incidents", type_="unique")
    op.create_unique_constraint("uq_incidents_year_number", "incidents", ["year", "number"])
    op.drop_column("incidents", "kind")
    op.drop_index(op.f("ix_task_dependencies_blocked_by_id"), table_name="task_dependencies")
    op.drop_table("task_dependencies")
    op.drop_index("uq_incident_links_one_parent", table_name="incident_links")
    op.drop_index(op.f("ix_incident_links_target_id"), table_name="incident_links")
    op.drop_index(op.f("ix_incident_links_source_id"), table_name="incident_links")
    op.drop_table("incident_links")
