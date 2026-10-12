"""Add versioned scenario workflows and durable run state."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scenario_draft",
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("workflow", sa.JSON(), nullable=False),
        sa.Column("layout", sa.JSON(), nullable=False),
        sa.Column("test_cases", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenario_definition.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("scenario_id"),
        if_not_exists=True,
    )
    op.create_table(
        "scenario_version",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenario_definition.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scenario_id", "version", name="uq_scenario_version"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_scenario_version_scenario_id"),
        "scenario_version",
        ["scenario_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "scenario_workflow_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("scenario_title", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("agent_snapshots", sa.JSON(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("test_config", sa.JSON(), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("is_test", sa.Boolean(), nullable=False),
        sa.Column("result_ref", sa.String(length=1024), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversation.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenario_definition.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "idempotency_key", name="uq_scenario_run_idempotency"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_scenario_workflow_run_conversation_id"),
        "scenario_workflow_run",
        ["conversation_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_scenario_workflow_run_owner_user_id"),
        "scenario_workflow_run",
        ["owner_user_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_scenario_workflow_run_scenario_id"),
        "scenario_workflow_run",
        ["scenario_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_scenario_workflow_run_status"),
        "scenario_workflow_run",
        ["status"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_scenario_workflow_run_workspace_id"),
        "scenario_workflow_run",
        ["workspace_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "scenario_step_run",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("input_ref", sa.String(length=1024), nullable=True),
        sa.Column("output_ref", sa.String(length=1024), nullable=True),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("decision", sa.Boolean(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["scenario_workflow_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "node_id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_scenario_step_run_operation_id"),
        "scenario_step_run",
        ["operation_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "scenario_run_event",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=80), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["scenario_workflow_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "sequence"),
        if_not_exists=True,
    )
    op.create_table(
        "scenario_outbox",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["scenario_workflow_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_scenario_outbox_delivered"),
        "scenario_outbox",
        ["delivered"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_scenario_outbox_run_id"),
        "scenario_outbox",
        ["run_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "scenario_artifact",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=80), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["file_artifact.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["scenario_workflow_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "artifact_id"),
        if_not_exists=True,
    )
    op.create_table(
        "scenario_execution",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["scenario_workflow_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "execution_id"),
        if_not_exists=True,
    )

    definitions = sa.table(
        "scenario_definition",
        sa.column("id", sa.String),
        sa.column("version", sa.Integer),
        sa.column("prompt_template", sa.Text),
        sa.column("input_schema", sa.JSON),
        sa.column("tool_policy", sa.JSON),
        sa.column("default_connector_bindings", sa.JSON),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("owner_user_id", sa.String),
    )
    versions = sa.table(
        "scenario_version",
        sa.column("id", sa.String),
        sa.column("scenario_id", sa.String),
        sa.column("version", sa.Integer),
        sa.column("kind", sa.String),
        sa.column("definition", sa.JSON),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("created_by", sa.String),
    )
    connection = op.get_bind()
    for definition in connection.execute(sa.select(definitions)).mappings():
        existing = connection.execute(
            sa.select(versions.c.id).where(
                versions.c.scenario_id == definition["id"],
                versions.c.version == definition["version"],
            )
        ).first()
        if existing is not None:
            continue
        connection.execute(
            versions.insert().values(
                id=str(uuid4()),
                scenario_id=definition["id"],
                version=definition["version"],
                kind="legacy",
                definition={
                    key: definition[key]
                    for key in (
                        "prompt_template",
                        "input_schema",
                        "tool_policy",
                        "default_connector_bindings",
                    )
                },
                created_at=definition["created_at"],
                created_by=definition["owner_user_id"],
            )
        )


def downgrade() -> None:
    op.drop_table("scenario_execution")
    op.drop_table("scenario_artifact")
    op.drop_table("scenario_outbox")
    op.drop_table("scenario_run_event")
    op.drop_table("scenario_step_run")
    op.drop_table("scenario_workflow_run")
    op.drop_table("scenario_version")
    op.drop_table("scenario_draft")
