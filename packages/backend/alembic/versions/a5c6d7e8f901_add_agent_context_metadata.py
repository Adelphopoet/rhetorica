"""Persist context projection independently of SDK history and user transcript."""

import sqlalchemy as sa
from alembic import op

revision = "a5c6d7e8f901"
down_revision = "f4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_agent_session",
        sa.Column("context_metadata", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("chat_agent_session", "context_metadata")
