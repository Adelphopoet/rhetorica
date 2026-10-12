"""Add optional LLM nucleus sampling setting without changing existing defaults."""

import sqlalchemy as sa
from alembic import op

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_connector_config") as batch:
        batch.add_column(sa.Column("top_p", sa.Float(), nullable=True))
        batch.create_check_constraint("ck_llm_connector_top_p", "top_p >= 0 AND top_p <= 1")


def downgrade() -> None:
    with op.batch_alter_table("llm_connector_config") as batch:
        batch.drop_constraint("ck_llm_connector_top_p", type_="check")
        batch.drop_column("top_p")
