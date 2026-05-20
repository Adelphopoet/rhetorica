"""add evidence payload to chat_message

Revision ID: a12d5c6e7f89
Revises: 7b3c2d1e9f44
Create Date: 2026-04-02 11:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a12d5c6e7f89"
down_revision: str | None = "7b3c2d1e9f44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "claims",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'::json"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "evidence",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'::json"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "claim_evidence_links",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'::json"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "uncertainty_flags",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'::json"),
            )
        )

    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.alter_column("claims", server_default=None)
        batch_op.alter_column("evidence", server_default=None)
        batch_op.alter_column("claim_evidence_links", server_default=None)
        batch_op.alter_column("uncertainty_flags", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.drop_column("uncertainty_flags")
        batch_op.drop_column("claim_evidence_links")
        batch_op.drop_column("evidence")
        batch_op.drop_column("claims")

