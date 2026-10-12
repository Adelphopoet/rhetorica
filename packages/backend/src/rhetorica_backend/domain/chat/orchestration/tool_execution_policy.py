"""Shared tool execution policy for tool calls and fatal loop stops."""

from __future__ import annotations

import json
from typing import Any

from rhetorica_backend.domain.chat.orchestration.models import (
    ExecuteToolCallPlan,
    RejectRepeatedToolCallPlan,
    ToolCallExecutionPlan,
)


class ToolExecutionPolicy:
    """Enforce the exact repeated-call side-effect boundary."""

    _MAX_IDENTICAL_TOOL_EXECUTIONS = 2

    def plan_tool_call(
        self,
        *,
        state: Any,
        tool_name: str,
        args_json: str,
        tool_call_id: str,
    ) -> ToolCallExecutionPlan:
        call_signature = (tool_name, args_json)
        previous_executions = self._matching_executions(
            state=state,
            call_signature=call_signature,
        )
        previous_execution_count = len(previous_executions)
        if previous_execution_count >= self._MAX_IDENTICAL_TOOL_EXECUTIONS:
            state.add_warning("llm_tool_loop_identical_call_limit_reached")
            return RejectRepeatedToolCallPlan(
                tool_call_id=tool_call_id,
                call_signature=call_signature,
                tool_result=self._identical_call_limit_error(
                    tool_name=tool_name,
                    args_json=args_json,
                    previous_executions=previous_executions,
                    blocked_call_signatures=self._blocked_call_signatures(
                        state=state,
                        tool_name=tool_name,
                    ),
                ),
            )
        return ExecuteToolCallPlan(
            tool_call_id=tool_call_id,
            call_signature=call_signature,
        )

    @staticmethod
    def _matching_executions(
        *,
        state: Any,
        call_signature: tuple[str, str],
    ) -> list[Any]:
        tool_name, args_json = call_signature
        return [
            entry
            for entry in state.executed_tool_call_ledger
            if entry.tool_name == tool_name
            and json.dumps(
                entry.arguments,
                sort_keys=True,
                ensure_ascii=False,
            )
            == args_json
        ]

    @classmethod
    def _blocked_call_signatures(
        cls,
        *,
        state: Any,
        tool_name: str,
    ) -> list[dict[str, Any]]:
        executions_by_args: dict[str, list[Any]] = {}
        for entry in state.executed_tool_call_ledger:
            if entry.tool_name != tool_name:
                continue
            args_json = json.dumps(
                entry.arguments,
                sort_keys=True,
                ensure_ascii=False,
            )
            executions_by_args.setdefault(args_json, []).append(entry)

        return [
            cls._execution_summary(args_json=args_json, executions=executions)
            for args_json, executions in sorted(executions_by_args.items())
            if len(executions) >= cls._MAX_IDENTICAL_TOOL_EXECUTIONS
        ]

    @staticmethod
    def _execution_summary(
        *,
        args_json: str,
        executions: list[Any],
    ) -> dict[str, Any]:
        artifact_ids = list(
            dict.fromkeys(
                entry.outcome.artifact_id for entry in executions if entry.outcome.artifact_id
            )
        )
        result_sha256s = list(
            dict.fromkeys(
                entry.outcome.artifact_sha256
                for entry in executions
                if entry.outcome.artifact_sha256
            )
        )
        all_results_hashed = len(result_sha256s) > 0 and all(
            entry.outcome.artifact_sha256 for entry in executions
        )
        previous_results_identical = len(result_sha256s) == 1 if all_results_hashed else None
        return {
            "arguments": json.loads(args_json),
            "previous_execution_count": len(executions),
            "previous_artifact_ids": artifact_ids,
            "previous_result_sha256s": result_sha256s,
            "previous_results_identical": previous_results_identical,
        }

    @staticmethod
    def _identical_call_limit_error(
        *,
        tool_name: str,
        args_json: str,
        previous_executions: list[Any],
        blocked_call_signatures: list[dict[str, Any]],
    ) -> dict[str, Any]:
        current_summary = ToolExecutionPolicy._execution_summary(
            args_json=args_json,
            executions=previous_executions,
        )
        previous_execution_count = len(previous_executions)
        if current_summary["previous_results_identical"] is True:
            result_statement = "результат идентичен, новых данных нет"
        else:
            result_statement = "повторное выполнение не запущено, поэтому новых данных нет"
        return {
            "ok": False,
            "error_code": "identical_tool_call_limit_reached",
            "error_category": "NON_RETRYABLE",
            "can_continue": True,
            "retryable": False,
            "retry_recommended": False,
            "blocked_for_turn": True,
            "duplicate": True,
            "new_evidence": False,
            "previous_execution_count": previous_execution_count,
            "previous_artifact_ids": current_summary["previous_artifact_ids"],
            "previous_result_sha256s": current_summary["previous_result_sha256s"],
            "previous_results_identical": current_summary["previous_results_identical"],
            "blocked_call_count": len(blocked_call_signatures),
            "blocked_call_signatures": blocked_call_signatures,
            "tool_name": tool_name,
            "arguments_json": args_json,
            "required_next_action": (
                "Use a previous artifact, change the complete argument set to a "
                "signature absent from blocked_call_signatures, or finalize."
            ),
            "user_message": (
                f"Этот запрос уже выполнялся {previous_execution_count} раза, "
                f"{result_statement}. Этот точный вызов заблокирован до конца "
                "текущего хода; не повторяйте его. Используйте предыдущий artifact, "
                "измените полный набор аргументов или завершите ответ."
            ),
        }
