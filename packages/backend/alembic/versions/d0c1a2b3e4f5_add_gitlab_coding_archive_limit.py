"""Add the configurable Coding source archive entry limit to GitLab connectors.

Revision ID: d0c1a2b3e4f5
Revises: c7a12a1a342e
"""

import sqlalchemy as sa
from alembic import op

revision = "d0c1a2b3e4f5"
down_revision = "c7a12a1a342e"
branch_labels = None
depends_on = None

DEFAULT_CODING_ARCHIVE_MAX_FILES = 20_000


def upgrade() -> None:
    with op.batch_alter_table("gitlab_connector_config") as batch:
        batch.add_column(
            sa.Column("coding_archive_max_files", sa.Integer(), nullable=True)
        )
    op.execute(
        sa.text(
            "UPDATE gitlab_connector_config "
            "SET coding_archive_max_files = :default "
            "WHERE coding_archive_max_files IS NULL"
        ).bindparams(default=DEFAULT_CODING_ARCHIVE_MAX_FILES)
    )
    with op.batch_alter_table("gitlab_connector_config") as batch:
        batch.alter_column(
            "coding_archive_max_files",
            nullable=False,
            server_default=sa.text(str(DEFAULT_CODING_ARCHIVE_MAX_FILES)),
        )


def downgrade() -> None:
    with op.batch_alter_table("gitlab_connector_config") as batch:
        batch.drop_column("coding_archive_max_files")
