"""add durable coding run, task and budget state

Revision ID: e0f1a2b3c4d5
Revises: d8e9f0a1b2c3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e0f1a2b3c4d5"
down_revision: str | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coding_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("scenario_id", sa.String(length=36), nullable=True),
        sa.Column("root_agent_id", sa.String(length=36), nullable=True),
        sa.Column("developer_agent_id", sa.String(length=36), nullable=False),
        sa.Column("qa_agent_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("repository_json", sa.JSON(), nullable=False),
        sa.Column("data_warehouse_json", sa.JSON(), nullable=True),
        sa.Column("environment_profile_id", sa.String(length=128), nullable=False),
        sa.Column("environment_profile_revision", sa.Integer(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("candidate_json", sa.JSON(), nullable=True),
        sa.Column("validation_report_id", sa.String(length=36), nullable=True),
        sa.Column("publication_receipt_id", sa.String(length=36), nullable=True),
        sa.Column("partial_result_json", sa.JSON(), nullable=True),
        sa.Column("principal_json", sa.JSON(), nullable=False),
        sa.Column("policy_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("environment_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversation.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "owner_user_id",
            "idempotency_key",
            name="uq_coding_run_owner_idempotency",
        ),
    )
    op.create_index("ix_coding_run_workspace_id", "coding_run", ["workspace_id"], unique=False)
    op.create_index("ix_coding_run_owner_user_id", "coding_run", ["owner_user_id"], unique=False)
    op.create_index("ix_coding_run_conversation_id", "coding_run", ["conversation_id"], unique=False)
    op.create_index("ix_coding_run_scenario_id", "coding_run", ["scenario_id"], unique=False)
    op.create_index("ix_coding_run_root_agent_id", "coding_run", ["root_agent_id"], unique=False)
    op.create_index("ix_coding_run_developer_agent_id", "coding_run", ["developer_agent_id"], unique=False)
    op.create_index("ix_coding_run_qa_agent_id", "coding_run", ["qa_agent_id"], unique=False)
    op.create_index("ix_coding_run_phase", "coding_run", ["phase"], unique=False)
    op.create_index("ix_coding_run_status", "coding_run", ["status"], unique=False)
    op.create_index(
        "ix_coding_run_owner_created",
        "coding_run",
        ["workspace_id", "owner_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_coding_run_status_deadline", "coding_run", ["status", "deadline"], unique=False
    )

    op.create_table(
        "coding_candidate",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=2048), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("base_sha", sa.String(length=64), nullable=False),
        sa.Column("changed_paths", sa.JSON(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["coding_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "digest", name="uq_coding_candidate_run_digest"),
    )
    op.create_index(
        "ix_coding_candidate_workspace_id", "coding_candidate", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_coding_candidate_owner_user_id", "coding_candidate", ["owner_user_id"], unique=False
    )
    op.create_index("ix_coding_candidate_run_id", "coding_candidate", ["run_id"], unique=False)
    op.create_index(
        "ix_coding_candidate_run_created",
        "coding_candidate",
        ["run_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "coding_agent_task",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("parent_task_id", sa.String(length=36), nullable=True),
        sa.Column("agent_profile_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("iterations_used", sa.Integer(), nullable=False),
        sa.Column("max_iterations", sa.Integer(), nullable=False),
        sa.Column("max_runtime_seconds", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("instruction_artifact_id", sa.String(length=2048), nullable=False),
        sa.Column("skill_version_digests", sa.JSON(), nullable=False),
        sa.Column("skill_version_bindings", sa.JSON(), nullable=False),
        sa.Column("skill_read_addresses", sa.JSON(), nullable=False),
        sa.Column("result_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint_ref", sa.String(length=2048), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("max_iterations > 0", name="ck_coding_agent_task_iterations_positive"),
        sa.CheckConstraint("max_runtime_seconds > 0", name="ck_coding_agent_task_runtime_positive"),
        sa.CheckConstraint("iterations_used >= 0", name="ck_coding_agent_task_iterations_nonnegative"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["coding_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_task_id"], ["coding_agent_task.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_coding_agent_task_workspace_id", "coding_agent_task", ["workspace_id"], unique=False)
    op.create_index("ix_coding_agent_task_owner_user_id", "coding_agent_task", ["owner_user_id"], unique=False)
    op.create_index("ix_coding_agent_task_run_id", "coding_agent_task", ["run_id"], unique=False)
    op.create_index("ix_coding_agent_task_parent_task_id", "coding_agent_task", ["parent_task_id"], unique=False)
    op.create_index("ix_coding_agent_task_agent_profile_id", "coding_agent_task", ["agent_profile_id"], unique=False)
    op.create_index("ix_coding_agent_task_status", "coding_agent_task", ["status"], unique=False)
    op.create_index("ix_coding_agent_task_run_role", "coding_agent_task", ["run_id", "role"], unique=False)
    op.create_index(
        "ix_coding_agent_task_owner_status",
        "coding_agent_task",
        ["workspace_id", "owner_user_id", "status"],
        unique=False,
    )

    op.create_table(
        "coding_budget_ledger",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("agent_profile_id", sa.String(length=36), nullable=False),
        sa.Column("max_iterations", sa.Integer(), nullable=False),
        sa.Column("reserved_iterations", sa.Integer(), nullable=False),
        sa.Column("first_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("max_iterations > 0", name="ck_coding_budget_max_positive"),
        sa.CheckConstraint("reserved_iterations >= 0", name="ck_coding_budget_reserved_nonnegative"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["coding_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "agent_profile_id", name="uq_coding_budget_run_profile"),
    )
    op.create_index("ix_coding_budget_ledger_workspace_id", "coding_budget_ledger", ["workspace_id"], unique=False)
    op.create_index("ix_coding_budget_ledger_owner_user_id", "coding_budget_ledger", ["owner_user_id"], unique=False)
    op.create_index("ix_coding_budget_ledger_run_id", "coding_budget_ledger", ["run_id"], unique=False)

    op.create_table(
        "coding_iteration_reservation",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("agent_profile_id", sa.String(length=36), nullable=False),
        sa.Column("invocation_id", sa.String(length=128), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["coding_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "agent_profile_id",
            "invocation_id",
            name="uq_coding_iteration_reservation_invocation",
        ),
    )
    op.create_index(
        "ix_coding_iteration_reservation_workspace_id",
        "coding_iteration_reservation",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_coding_iteration_reservation_owner_user_id",
        "coding_iteration_reservation",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_coding_iteration_reservation_run_id",
        "coding_iteration_reservation",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "coding_run_event",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=96), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["coding_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["coding_agent_task.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("run_id", "sequence"),
    )
    op.create_index("ix_coding_run_event_workspace_id", "coding_run_event", ["workspace_id"], unique=False)
    op.create_index("ix_coding_run_event_owner_user_id", "coding_run_event", ["owner_user_id"], unique=False)
    op.create_index("ix_coding_run_event_task_id", "coding_run_event", ["task_id"], unique=False)

    op.create_table(
        "coding_validation_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("candidate_digest", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("mandatory", sa.Boolean(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("artifact_id", sa.String(length=2048), nullable=True),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["coding_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["coding_agent_task.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "id", name="uq_coding_validation_evidence_run_id"),
    )
    op.create_index(
        "ix_coding_validation_evidence_workspace_id",
        "coding_validation_evidence",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_coding_validation_evidence_owner_user_id",
        "coding_validation_evidence",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_coding_validation_evidence_run_id",
        "coding_validation_evidence",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_coding_validation_evidence_task_id",
        "coding_validation_evidence",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        "ix_coding_validation_candidate",
        "coding_validation_evidence",
        ["run_id", "candidate_digest"],
        unique=False,
    )

    op.create_table(
        "coding_validation_report",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_digest", sa.String(length=64), nullable=False),
        sa.Column("report_digest", sa.String(length=64), nullable=False),
        sa.Column("qa_agent_id", sa.String(length=36), nullable=False),
        sa.Column("qa_verdict_json", sa.JSON(), nullable=True),
        sa.Column("checks_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["coding_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "candidate_digest",
            name="uq_coding_validation_report_candidate",
        ),
    )
    op.create_index(
        "ix_coding_validation_report_workspace_id",
        "coding_validation_report",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_coding_validation_report_owner_user_id",
        "coding_validation_report",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_coding_validation_report_run_id",
        "coding_validation_report",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_coding_validation_report_run",
        "coding_validation_report",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "coding_run_outbox",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["coding_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_coding_run_outbox_workspace_id", "coding_run_outbox", ["workspace_id"], unique=False)
    op.create_index("ix_coding_run_outbox_owner_user_id", "coding_run_outbox", ["owner_user_id"], unique=False)
    op.create_index("ix_coding_run_outbox_run_id", "coding_run_outbox", ["run_id"], unique=False)
    op.create_index("ix_coding_run_outbox_delivered", "coding_run_outbox", ["delivered"], unique=False)
    op.create_index(
        "ix_coding_run_outbox_delivery", "coding_run_outbox", ["delivered", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_coding_run_outbox_delivery", table_name="coding_run_outbox")
    op.drop_index("ix_coding_run_outbox_delivered", table_name="coding_run_outbox")
    op.drop_index("ix_coding_run_outbox_run_id", table_name="coding_run_outbox")
    op.drop_index("ix_coding_run_outbox_owner_user_id", table_name="coding_run_outbox")
    op.drop_index("ix_coding_run_outbox_workspace_id", table_name="coding_run_outbox")
    op.drop_table("coding_run_outbox")
    op.drop_index("ix_coding_validation_report_run", table_name="coding_validation_report")
    op.drop_index("ix_coding_validation_report_run_id", table_name="coding_validation_report")
    op.drop_index(
        "ix_coding_validation_report_owner_user_id", table_name="coding_validation_report"
    )
    op.drop_index(
        "ix_coding_validation_report_workspace_id", table_name="coding_validation_report"
    )
    op.drop_table("coding_validation_report")
    op.drop_index("ix_coding_validation_candidate", table_name="coding_validation_evidence")
    op.drop_index("ix_coding_validation_evidence_task_id", table_name="coding_validation_evidence")
    op.drop_index("ix_coding_validation_evidence_run_id", table_name="coding_validation_evidence")
    op.drop_index("ix_coding_validation_evidence_owner_user_id", table_name="coding_validation_evidence")
    op.drop_index("ix_coding_validation_evidence_workspace_id", table_name="coding_validation_evidence")
    op.drop_table("coding_validation_evidence")
    op.drop_index("ix_coding_run_event_task_id", table_name="coding_run_event")
    op.drop_index("ix_coding_run_event_owner_user_id", table_name="coding_run_event")
    op.drop_index("ix_coding_run_event_workspace_id", table_name="coding_run_event")
    op.drop_table("coding_run_event")
    op.drop_index("ix_coding_iteration_reservation_run_id", table_name="coding_iteration_reservation")
    op.drop_index("ix_coding_iteration_reservation_owner_user_id", table_name="coding_iteration_reservation")
    op.drop_index("ix_coding_iteration_reservation_workspace_id", table_name="coding_iteration_reservation")
    op.drop_table("coding_iteration_reservation")
    op.drop_index("ix_coding_budget_ledger_run_id", table_name="coding_budget_ledger")
    op.drop_index("ix_coding_budget_ledger_owner_user_id", table_name="coding_budget_ledger")
    op.drop_index("ix_coding_budget_ledger_workspace_id", table_name="coding_budget_ledger")
    op.drop_table("coding_budget_ledger")
    op.drop_index("ix_coding_agent_task_owner_status", table_name="coding_agent_task")
    op.drop_index("ix_coding_agent_task_run_role", table_name="coding_agent_task")
    op.drop_index("ix_coding_agent_task_status", table_name="coding_agent_task")
    op.drop_index("ix_coding_agent_task_agent_profile_id", table_name="coding_agent_task")
    op.drop_index("ix_coding_agent_task_parent_task_id", table_name="coding_agent_task")
    op.drop_index("ix_coding_agent_task_run_id", table_name="coding_agent_task")
    op.drop_index("ix_coding_agent_task_owner_user_id", table_name="coding_agent_task")
    op.drop_index("ix_coding_agent_task_workspace_id", table_name="coding_agent_task")
    op.drop_table("coding_agent_task")
    op.drop_index("ix_coding_candidate_run_created", table_name="coding_candidate")
    op.drop_index("ix_coding_candidate_run_id", table_name="coding_candidate")
    op.drop_index("ix_coding_candidate_owner_user_id", table_name="coding_candidate")
    op.drop_index("ix_coding_candidate_workspace_id", table_name="coding_candidate")
    op.drop_table("coding_candidate")
    op.drop_index("ix_coding_run_status_deadline", table_name="coding_run")
    op.drop_index("ix_coding_run_owner_created", table_name="coding_run")
    op.drop_index("ix_coding_run_status", table_name="coding_run")
    op.drop_index("ix_coding_run_phase", table_name="coding_run")
    op.drop_index("ix_coding_run_qa_agent_id", table_name="coding_run")
    op.drop_index("ix_coding_run_developer_agent_id", table_name="coding_run")
    op.drop_index("ix_coding_run_root_agent_id", table_name="coding_run")
    op.drop_index("ix_coding_run_scenario_id", table_name="coding_run")
    op.drop_index("ix_coding_run_conversation_id", table_name="coding_run")
    op.drop_index("ix_coding_run_owner_user_id", table_name="coding_run")
    op.drop_index("ix_coding_run_workspace_id", table_name="coding_run")
    op.drop_table("coding_run")
