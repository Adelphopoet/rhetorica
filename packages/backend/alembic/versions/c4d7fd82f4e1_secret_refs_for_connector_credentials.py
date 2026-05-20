"""secret refs for connector credentials

Revision ID: c4d7fd82f4e1
Revises: fee8a509d08c
Create Date: 2026-04-01 23:10:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d7fd82f4e1"
down_revision: str | None = "fee8a509d08c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("connector_config", schema=None) as batch_op:
        batch_op.add_column(sa.Column("password_secret_ref", sa.String(length=512), nullable=True))

    with op.batch_alter_table("llm_connector_config", schema=None) as batch_op:
        batch_op.add_column(sa.Column("api_key_secret_ref", sa.String(length=512), nullable=True))

    with op.batch_alter_table("openmetadata_connector_config", schema=None) as batch_op:
        batch_op.add_column(sa.Column("api_token_secret_ref", sa.String(length=512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("openmetadata_connector_config", schema=None) as batch_op:
        batch_op.drop_column("api_token_secret_ref")

    with op.batch_alter_table("llm_connector_config", schema=None) as batch_op:
        batch_op.drop_column("api_key_secret_ref")

    with op.batch_alter_table("connector_config", schema=None) as batch_op:
        batch_op.drop_column("password_secret_ref")
