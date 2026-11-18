"""The attack flow's steps are the attack's anatomy (Phishing, Toolkit, Steals logins, Infrastructure…)
rather than ten stages grouping ATT&CK's tactics: analysts' calls and tasks move to the matching step.

A stage with no single matching step (Prepares, Gets in) loses its calls and leaves its tasks off the flow.
Going back, a step with no stage of its own (Cloud) does the same, and where several steps fold into one
stage only the call on the first is kept, since a record has one call per stage.

Revision ID: 0023
Revises: 0022
Created: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UP = {
    "runs": "toolkit",
    "stays": "keeps_access",
    "hides": "hides",
    "takes_credentials": "steals_logins",
    "moves": "moves",
    "talks_home": "infrastructure",
    "takes": "takes_out",
    "impact": "destroys",
}
DOWN = {
    "exploit": "gets_in",
    "phishing": "gets_in",
    "credentials": "gets_in",
    "toolkit": "runs",
    "keeps_access": "stays",
    "admin": "stays",
    "hides": "hides",
    "steals_logins": "takes_credentials",
    "moves": "moves",
    "looks_around": "moves",
    "infrastructure": "talks_home",
    "messaging": "talks_home",
    "takes_out": "takes",
    "gathers": "takes",
    "storage": "takes",
    "destroys": "impact",
    "extorts": "impact",
}


def _move(mapping: dict[str, str]) -> None:
    conn = op.get_bind()
    tasks = sa.table("tasks", sa.column("stage", sa.String))
    calls = sa.table(
        "flow_stages",
        sa.column("id", sa.Uuid),
        sa.column("incident_id", sa.Uuid),
        sa.column("stage", sa.String),
    )
    conn.execute(sa.update(tasks).where(tasks.c.stage.not_in(list(mapping))).values(stage=None))
    conn.execute(sa.delete(calls).where(calls.c.stage.not_in(list(mapping))))
    # one call per stage and record: of several that fold into one, keep the first in the mapping's order
    kept: set[tuple[object, str]] = set()
    rows = conn.execute(sa.select(calls.c.id, calls.c.incident_id, calls.c.stage)).all()
    order = list(mapping)
    for row in sorted(rows, key=lambda r: order.index(r.stage)):
        target = (row.incident_id, mapping[row.stage])
        if target in kept:
            conn.execute(sa.delete(calls).where(calls.c.id == row.id))
        kept.add(target)
    # rename through a prefix so a step called as an old stage ("hides", "moves") isn't renamed twice
    for old, new in mapping.items():
        conn.execute(sa.update(tasks).where(tasks.c.stage == old).values(stage=f"~{new}"))
        conn.execute(sa.update(calls).where(calls.c.stage == old).values(stage=f"~{new}"))
    for table in (tasks, calls):
        conn.execute(
            sa.update(table).where(table.c.stage.like("~%")).values(stage=sa.func.substr(table.c.stage, 2))
        )


def upgrade() -> None:
    _move(UP)


def downgrade() -> None:
    _move(DOWN)
