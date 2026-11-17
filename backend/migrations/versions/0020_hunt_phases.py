"""Hunts run Hypothesis → Scope → Analyse → Report → Learn: Collect is now Scope, and Conclude gives way to
Report and Learn, as incidents end. Hunts, hunt playbooks' steps and decisions, and the playbooks already
applied to hunts move to the new names. Going back, Scope is Collect again and Report and Learn are
Conclude.

Revision ID: 0020
Revises: 0019
Created: 2026-11-17
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UP = {"collect": "scope", "conclude": "report"}
DOWN = {"scope": "collect", "report": "conclude", "learn": "conclude"}


def _move(names: dict[str, str]) -> None:
    conn = op.get_bind()
    old = list(names)
    rename = sa.text("UPDATE incidents SET phase = :new WHERE kind = 'hunt' AND phase = :old")
    for before, after in names.items():
        conn.execute(rename, {"new": after, "old": before})

    def outcomes(items: list[dict] | None) -> list[dict]:
        return [{**o, "phase": names.get(o.get("phase"), o.get("phase"))} for o in items or []]

    for step_id, phase, decided in conn.execute(
        sa.text(
            "SELECT s.id, s.phase, s.outcomes FROM playbook_steps s JOIN playbooks p ON p.id = s.playbook_id "
            "WHERE p.applies_to = 'hunt'"
        )
    ):
        if phase in old or outcomes(decided) != (decided or []):
            conn.execute(
                sa.text("UPDATE playbook_steps SET phase = :p, outcomes = CAST(:o AS jsonb) WHERE id = :id"),
                {"p": names.get(phase, phase), "o": json.dumps(outcomes(decided)), "id": step_id},
            )
    for row_id, steps in conn.execute(
        sa.text(
            "SELECT a.id, a.steps FROM incident_playbooks a JOIN incidents i ON i.id = a.incident_id "
            "WHERE i.kind = 'hunt'"
        )
    ):
        moved = [
            {**s, "phase": names.get(s.get("phase"), s.get("phase")), "outcomes": outcomes(s.get("outcomes"))}
            for s in steps or []
        ]
        if moved != steps:
            conn.execute(
                sa.text("UPDATE incident_playbooks SET steps = CAST(:s AS jsonb) WHERE id = :id"),
                {"s": json.dumps(moved), "id": row_id},
            )


def upgrade() -> None:
    _move(UP)


def downgrade() -> None:
    _move(DOWN)
