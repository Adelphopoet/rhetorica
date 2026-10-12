"""Add optional reasoning effort to LLM connectors."""

import sqlalchemy as sa
from alembic import op

revision = "a4b5c6d7e8f9"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_connector_config") as batch:
        batch.add_column(sa.Column("reasoning_effort", sa.String(length=16), nullable=True))
        batch.create_check_constraint(
            "ck_llm_connector_reasoning_effort",
            "reasoning_effort IN ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')",
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_connector_config") as batch:
        batch.drop_constraint("ck_llm_connector_reasoning_effort", type_="check")
        batch.drop_column("reasoning_effort")
