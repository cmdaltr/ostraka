"""Chat channels in Teams or Slack: a team's channel may be either, and an incident may have its own.

`teams.teams_webhook` becomes `teams.channel_webhook` beside `channel_kind`; the channels already
connected are Teams Workflows webhooks. Going back, a Slack channel can't be kept (the old code only
posts to Teams), so it's disconnected, and incidents lose their channels.

Revision ID: 0028
Revises: 0027
Created: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("teams", "teams_webhook", new_column_name="channel_webhook")
    op.add_column("teams", sa.Column("channel_kind", sa.String(8), nullable=False, server_default=""))
    op.execute("UPDATE teams SET channel_kind = 'teams' WHERE channel_webhook <> ''")
    op.add_column("incidents", sa.Column("channel_webhook", sa.Text(), nullable=False, server_default=""))
    op.add_column("incidents", sa.Column("channel_kind", sa.String(8), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("incidents", "channel_kind")
    op.drop_column("incidents", "channel_webhook")
    op.execute("UPDATE teams SET channel_webhook = '' WHERE channel_kind = 'slack'")
    op.drop_column("teams", "channel_kind")
    op.alter_column("teams", "channel_webhook", new_column_name="teams_webhook")
