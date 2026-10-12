"""share one root-agent budget across a coding run

Revision ID: e1f2a3b4c5d6
Revises: e0f1a2b3c4d5
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


coding_run = sa.table(
    "coding_run",
    sa.column("id", sa.String(length=36)),
    sa.column("root_agent_id", sa.String(length=36)),
    sa.column("policy_snapshot_json", sa.JSON()),
)
coding_budget_ledger = sa.table(
    "coding_budget_ledger",
    sa.column("id", sa.String(length=36)),
    sa.column("run_id", sa.String(length=36)),
    sa.column("budget_owner_agent_id", sa.String(length=36)),
    sa.column("max_iterations", sa.Integer()),
    sa.column("reserved_iterations", sa.Integer()),
    sa.column("first_attempt_at", sa.DateTime(timezone=True)),
    sa.column("deadline", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
coding_iteration_reservation = sa.table(
    "coding_iteration_reservation",
    sa.column("run_id", sa.String(length=36)),
    sa.column("budget_owner_agent_id", sa.String(length=36)),
)


def upgrade() -> None:
    # Keep existing accepted runs safe. They may have one legacy ledger per
    # executing specialist. The shared maximum comes from the immutable root
    # policy captured at admission; only consumed counters are additive.
    op.add_column(
        "coding_budget_ledger",
        sa.Column("budget_owner_agent_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "coding_iteration_reservation",
        sa.Column("budget_owner_agent_id", sa.String(length=36), nullable=True),
    )
    _collapse_legacy_ledgers(op.get_bind())
    op.drop_constraint(
        "uq_coding_budget_run_profile", "coding_budget_ledger", type_="unique"
    )
    op.drop_constraint(
        "uq_coding_iteration_reservation_invocation",
        "coding_iteration_reservation",
        type_="unique",
    )
    op.alter_column("coding_budget_ledger", "budget_owner_agent_id", nullable=False)
    op.alter_column(
        "coding_iteration_reservation", "budget_owner_agent_id", nullable=False
    )
    op.create_unique_constraint(
        "uq_coding_budget_run_owner",
        "coding_budget_ledger",
        ["run_id", "budget_owner_agent_id"],
    )
    op.create_unique_constraint(
        "uq_coding_iteration_reservation_invocation",
        "coding_iteration_reservation",
        ["run_id", "budget_owner_agent_id", "invocation_id"],
    )
    op.drop_column("coding_budget_ledger", "agent_profile_id")
    op.drop_column("coding_iteration_reservation", "agent_profile_id")


def downgrade() -> None:
    # A collapsed ledger cannot be split faithfully. Map it to the budget owner
    # so schema rollback remains possible without inventing extra allowances.
    op.add_column(
        "coding_budget_ledger",
        sa.Column("agent_profile_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "coding_iteration_reservation",
        sa.Column("agent_profile_id", sa.String(length=36), nullable=True),
    )
    op.execute(
        "UPDATE coding_budget_ledger SET agent_profile_id = budget_owner_agent_id"
    )
    op.execute(
        "UPDATE coding_iteration_reservation SET agent_profile_id = budget_owner_agent_id"
    )
    op.drop_constraint(
        "uq_coding_budget_run_owner", "coding_budget_ledger", type_="unique"
    )
    op.drop_constraint(
        "uq_coding_iteration_reservation_invocation",
        "coding_iteration_reservation",
        type_="unique",
    )
    op.alter_column("coding_budget_ledger", "agent_profile_id", nullable=False)
    op.alter_column("coding_iteration_reservation", "agent_profile_id", nullable=False)
    op.create_unique_constraint(
        "uq_coding_budget_run_profile",
        "coding_budget_ledger",
        ["run_id", "agent_profile_id"],
    )
    op.create_unique_constraint(
        "uq_coding_iteration_reservation_invocation",
        "coding_iteration_reservation",
        ["run_id", "agent_profile_id", "invocation_id"],
    )
    op.drop_column("coding_budget_ledger", "budget_owner_agent_id")
    op.drop_column("coding_iteration_reservation", "budget_owner_agent_id")


def _collapse_legacy_ledgers(connection: sa.Connection) -> None:
    run_budgets: dict[str, tuple[str, int]] = {}
    run_rows = connection.execute(
        sa.select(
            coding_run.c.id,
            coding_run.c.root_agent_id,
            coding_run.c.policy_snapshot_json,
        )
    ).mappings()
    for run in run_rows:
        run_budgets[str(run["id"])] = _root_budget_from_snapshot(run)

    for run_id, (budget_owner_agent_id, _max_iterations) in run_budgets.items():
        connection.execute(
            sa.update(coding_budget_ledger)
            .where(coding_budget_ledger.c.run_id == run_id)
            .values(budget_owner_agent_id=budget_owner_agent_id)
        )
        connection.execute(
            sa.update(coding_iteration_reservation)
            .where(coding_iteration_reservation.c.run_id == run_id)
            .values(budget_owner_agent_id=budget_owner_agent_id)
        )

    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    ledger_rows = connection.execute(
        sa.select(
            coding_budget_ledger.c.id,
            coding_budget_ledger.c.run_id,
            coding_budget_ledger.c.budget_owner_agent_id,
            coding_budget_ledger.c.reserved_iterations,
            coding_budget_ledger.c.first_attempt_at,
            coding_budget_ledger.c.deadline,
            coding_budget_ledger.c.updated_at,
        )
    ).mappings()
    for ledger in ledger_rows:
        key = (str(ledger["run_id"]), str(ledger["budget_owner_agent_id"]))
        groups.setdefault(key, []).append(ledger)

    for (run_id, budget_owner_agent_id), ledgers in groups.items():
        if run_id not in run_budgets:
            raise RuntimeError("coding_budget_migration_run_snapshot_missing")
        expected_owner, max_iterations = run_budgets[run_id]
        if expected_owner != budget_owner_agent_id:
            raise RuntimeError("coding_budget_migration_owner_mismatch")
        keep_id = min(str(ledger["id"]) for ledger in ledgers)
        first_attempts = [
            ledger["first_attempt_at"]
            for ledger in ledgers
            if ledger["first_attempt_at"] is not None
        ]
        deadlines = [ledger["deadline"] for ledger in ledgers]
        updated_at = [ledger["updated_at"] for ledger in ledgers]
        if not deadlines or any(deadline is None for deadline in deadlines):
            raise RuntimeError("coding_budget_migration_deadline_missing")
        connection.execute(
            sa.update(coding_budget_ledger)
            .where(coding_budget_ledger.c.id == keep_id)
            .values(
                max_iterations=max_iterations,
                reserved_iterations=sum(
                    int(ledger["reserved_iterations"]) for ledger in ledgers
                ),
                first_attempt_at=min(first_attempts) if first_attempts else None,
                deadline=min(deadlines),
                updated_at=max(updated_at),
            )
        )
        remove_ids = [str(ledger["id"]) for ledger in ledgers if str(ledger["id"]) != keep_id]
        if remove_ids:
            connection.execute(
                sa.delete(coding_budget_ledger).where(
                    coding_budget_ledger.c.id.in_(remove_ids)
                )
            )


def _root_budget_from_snapshot(run: Mapping[str, Any]) -> tuple[str, int]:
    raw_snapshot = run["policy_snapshot_json"]
    snapshot = json.loads(raw_snapshot) if isinstance(raw_snapshot, str) else raw_snapshot
    if not isinstance(snapshot, dict):
        raise TypeError("coding_budget_migration_policy_snapshot_invalid")
    snapshot_root_id = snapshot.get("root_agent_id")
    executing_agent_id = snapshot.get("executing_agent_id")
    budget_owner_agent_id = run["root_agent_id"] or snapshot_root_id or executing_agent_id
    if not isinstance(budget_owner_agent_id, str) or not budget_owner_agent_id:
        raise RuntimeError("coding_budget_migration_owner_missing")
    if snapshot_root_id == budget_owner_agent_id:
        settings = snapshot.get("root_settings")
    elif executing_agent_id == budget_owner_agent_id:
        settings = snapshot.get("executing_settings")
    else:
        raise RuntimeError("coding_budget_migration_owner_snapshot_mismatch")
    if not isinstance(settings, dict):
        raise TypeError("coding_budget_migration_settings_missing")
    max_iterations = settings.get("max_iterations")
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations < 1:
        raise RuntimeError("coding_budget_migration_max_iterations_invalid")
    return budget_owner_agent_id, max_iterations
