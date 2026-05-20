"""initial schema

Revision ID: fee8a509d08c
Revises:
Create Date: 2026-04-01 20:55:46.268168

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'fee8a509d08c'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create query_execution_history table
    op.create_table(
        'query_execution_history',
        sa.Column('execution_id', sa.String(36), nullable=False),
        sa.Column('workspace_id', sa.String(128), nullable=False, index=True),
        sa.Column('engine', sa.String(32), nullable=False, index=True),
        sa.Column('status', sa.String(32), nullable=False, index=True),
        sa.Column('submitted_sql', sa.Text, nullable=False),
        sa.Column('normalized_sql', sa.Text, nullable=True),
        sa.Column('timeout_ms', sa.Integer, nullable=False),
        sa.Column('row_limit', sa.Integer, nullable=False),
        sa.Column('row_count', sa.Integer, nullable=True),
        sa.Column('duration_ms', sa.Integer, nullable=True),
        sa.Column('query_id', sa.String(255), nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('warnings', sa.JSON, nullable=False, default=list),
        sa.Column('violations', sa.JSON, nullable=False, default=list),
        sa.Column('connection_summary', sa.JSON, nullable=False, default=dict),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('execution_id')
    )

    # Create connector_config table
    op.create_table(
        'connector_config',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('workspace_id', sa.String(128), nullable=False, index=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('engine', sa.String(32), nullable=False, index=True),
        sa.Column('host', sa.String(512), nullable=False),
        sa.Column('port', sa.Integer, nullable=False),
        sa.Column('username', sa.String(255), nullable=False),
        sa.Column('password', sa.String(2048), nullable=True),
        sa.Column('database', sa.String(255), nullable=True),
        sa.Column('catalog', sa.String(255), nullable=True),
        sa.Column('schema_name', sa.String(255), nullable=True),
        sa.Column('secure', sa.Boolean, nullable=False, default=False),
        sa.Column('verify', sa.Boolean, nullable=False, default=True),
        sa.Column('extra_settings', sa.JSON, nullable=False, default=dict),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'name', name='uq_connector_workspace_name')
    )

    # Create llm_connector_config table
    op.create_table(
        'llm_connector_config',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('workspace_id', sa.String(128), nullable=False, index=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('base_url', sa.String(1024), nullable=False),
        sa.Column('model', sa.String(255), nullable=False),
        sa.Column('api_key', sa.String(4096), nullable=True),
        sa.Column('endpoint_path', sa.String(255), nullable=False, default='/chat/completions'),
        sa.Column('temperature', sa.Float, nullable=True),
        sa.Column('max_tokens', sa.Integer, nullable=True),
        sa.Column('headers', sa.JSON, nullable=False, default=dict),
        sa.Column('extra_settings', sa.JSON, nullable=False, default=dict),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'name', name='uq_llm_connector_workspace_name')
    )

    # Create openmetadata_connector_config table
    op.create_table(
        'openmetadata_connector_config',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('workspace_id', sa.String(128), nullable=False, index=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('provider', sa.String(64), nullable=False, default='openmetadata'),
        sa.Column('base_url', sa.String(1024), nullable=False),
        sa.Column('api_token', sa.String(4096), nullable=True),
        sa.Column('verify_tls', sa.Boolean, nullable=False, default=True),
        sa.Column('mcp_tables_url', sa.String(1024), nullable=True),
        sa.Column('service_name', sa.String(255), nullable=True),
        sa.Column('database_name', sa.String(255), nullable=True),
        sa.Column('schema_name', sa.String(255), nullable=True),
        sa.Column('table_limit', sa.Integer, nullable=False, default=200),
        sa.Column('fields', sa.String(255), nullable=False, default='columns,database,databaseSchema'),
        sa.Column('extra_settings', sa.JSON, nullable=False, default=dict),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'name', name='uq_openmetadata_connector_workspace_name')
    )

    # Create chat_conversation table
    op.create_table(
        'chat_conversation',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('workspace_id', sa.String(128), nullable=False, index=True),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Create chat_message table
    op.create_table(
        'chat_message',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('chat_conversation.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('role', sa.String(32), nullable=False, index=True),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('generated_sql', sa.Text, nullable=True),
        sa.Column('execution_id', sa.String(36), nullable=True),
        sa.Column('context_tables', sa.JSON, nullable=False, default=list),
        sa.Column('metadata_sources', sa.JSON, nullable=False, default=list),
        sa.Column('selected_connector', sa.String(255), nullable=True),
        sa.Column('warnings', sa.JSON, nullable=False, default=list),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'created_at', 'id', name='uq_chat_message_order')
    )


def downgrade() -> None:
    op.drop_table('chat_message')
    op.drop_table('chat_conversation')
    op.drop_table('openmetadata_connector_config')
    op.drop_table('llm_connector_config')
    op.drop_table('connector_config')
    op.drop_table('query_execution_history')
