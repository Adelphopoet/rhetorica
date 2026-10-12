"""Share existing SDK session tables between conversations and coding tasks."""

import sqlalchemy as sa
from alembic import op

revision = "8b32f90d6a41"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    # Development startup may create current metadata before Alembic runs.
    # Only empty, exact-shape placeholders may be removed; never discard history.
    if "agent_session" in tables:
        expected = {
            "session_id",
            "conversation_id",
            "task_id",
            "next_sequence",
            "context_metadata",
            "created_at",
            "updated_at",
        }
        if {column["name"] for column in inspector.get_columns("agent_session")} != expected:
            raise RuntimeError("Unexpected pre-existing agent_session schema")
        if bind.execute(sa.text("SELECT 1 FROM agent_session LIMIT 1")).first():
            raise RuntimeError("Resolve pre-existing shared session history before migration")
        if "agent_session_item" in tables:
            if bind.execute(sa.text("SELECT 1 FROM agent_session_item LIMIT 1")).first():
                raise RuntimeError("Resolve pre-existing shared session items before migration")
            op.drop_table("agent_session_item")
        op.drop_table("agent_session")
    foreign_keys = inspector.get_foreign_keys("chat_agent_session")
    for key in foreign_keys:
        op.drop_constraint(key["name"], "chat_agent_session", type_="foreignkey")
    op.rename_table("chat_agent_session", "agent_session")
    op.rename_table("chat_agent_session_item", "agent_session_item")
    _rename_history_constraints(upgrade=True)
    op.alter_column("agent_session", "conversation_id", new_column_name="session_id")
    op.alter_column("agent_session_item", "conversation_id", new_column_name="session_id")
    op.add_column("agent_session", sa.Column("conversation_id", sa.String(36)))
    op.add_column("agent_session", sa.Column("task_id", sa.String(36)))
    op.execute("UPDATE agent_session SET conversation_id = session_id")
    op.create_foreign_key(
        "fk_agent_session_conversation",
        "agent_session",
        "chat_conversation",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_agent_session_task",
        "agent_session",
        "coding_agent_task",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_agent_session_conversation", "agent_session", ["conversation_id"]
    )
    op.create_unique_constraint("uq_agent_session_task", "agent_session", ["task_id"])
    op.create_check_constraint(
        "ck_agent_session_subject",
        "agent_session",
        "(conversation_id IS NOT NULL AND task_id IS NULL) OR (conversation_id IS NULL AND task_id IS NOT NULL)",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM agent_session WHERE task_id IS NOT NULL LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Cannot downgrade shared sessions while coding task history exists")
    op.drop_constraint("ck_agent_session_subject", "agent_session", type_="check")
    for name in ("fk_agent_session_conversation", "fk_agent_session_task"):
        op.drop_constraint(name, "agent_session", type_="foreignkey")
    for name in ("uq_agent_session_conversation", "uq_agent_session_task"):
        op.drop_constraint(name, "agent_session", type_="unique")
    op.drop_column("agent_session", "task_id")
    op.drop_column("agent_session", "conversation_id")
    op.alter_column("agent_session", "session_id", new_column_name="conversation_id")
    op.alter_column("agent_session_item", "session_id", new_column_name="conversation_id")
    _rename_history_constraints(upgrade=False)
    op.rename_table("agent_session", "chat_agent_session")
    op.rename_table("agent_session_item", "chat_agent_session_item")
    op.create_foreign_key(
        "chat_agent_session_conversation_id_fkey",
        "chat_agent_session",
        "chat_conversation",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )


def _rename_history_constraints(*, upgrade: bool) -> None:
    for table, names in (
        ("agent_session", ("ck_agent_session_next_sequence_non_negative",)),
        (
            "agent_session_item",
            ("ck_agent_session_item_sequence_non_negative", "uq_agent_session_item_sequence"),
        ),
    ):
        for new in names:
            old = new.replace("agent_session", "chat_agent_session")
            source, target = (old, new) if upgrade else (new, old)
            op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {source} TO {target}")
    old, new = (
        "ix_chat_agent_session_item_conversation_sequence",
        "ix_agent_session_item_conversation_sequence",
    )
    source, target = (old, new) if upgrade else (new, old)
    op.execute(f"ALTER INDEX {source} RENAME TO {target}")
