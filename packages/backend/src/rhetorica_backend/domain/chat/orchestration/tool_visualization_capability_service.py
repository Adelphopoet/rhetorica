"""Visualization-family orchestration tools."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.visualization import (
    VisualizationDataSource,
    VisualizationEncoding,
    VisualizationRationale,
    VisualizationSpec,
    VisualizationTransform,
)
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    SqlExecutionAttempt,
    ToolOrchestrationState,
)
from rhetorica_backend.domain.results.visualization_planner import VisualizationPlanner


class ToolVisualizationCapabilityService:
    """Validates model-owned visualization specs against SQL execution previews."""

    def __init__(self, *, visualization_planner: VisualizationPlanner | None = None) -> None:
        self._visualization_planner = visualization_planner or VisualizationPlanner()

    def execute_build_visualization(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
        db_connectors: list[ConnectorItem],
    ) -> dict[str, Any]:
        del request, db_connectors
        execution_id = self._required_str(arguments.get("execution_id"))
        if execution_id is None:
            state.add_warning("visualization_execution_id_missing")
            return build_tool_error(
                error_code="visualization_execution_id_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="build_visualization requires execution_id from a successful run_sql result.",
                retry_recommended=False,
                can_continue=True,
            )

        attempt = self._execution_attempt_by_id(state=state, execution_id=execution_id)
        if attempt is None:
            state.add_warning("visualization_execution_not_found")
            return build_tool_error(
                error_code="visualization_execution_not_found",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="No successful SQL execution was found for that execution_id.",
                retry_recommended=False,
                can_continue=True,
            )

        candidate = self._candidate_from_arguments(
            arguments=arguments,
            attempt=attempt,
        )
        if candidate is None:
            state.add_warning("visualization_invalid_arguments")
            return build_tool_error(
                error_code="visualization_invalid_arguments",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Visualization arguments are incomplete or invalid.",
                retry_recommended=False,
                can_continue=True,
            )

        visualization = self._visualization_planner.plan(
            execution=attempt.execution,
            llm_candidate=candidate.model_dump(mode="json"),
        )
        if visualization is None:
            state.add_warning("visualization_incompatible_with_execution")
            return build_tool_error(
                error_code="visualization_incompatible_with_execution",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Visualization spec is incompatible with the SQL execution preview.",
                retry_recommended=False,
                can_continue=True,
            )

        state.visualization_candidate = visualization
        state.visualization_candidates.append(visualization)
        return {
            "ok": True,
            "tool_name": "build_visualization",
            "execution_id": attempt.execution.execution_id,
            "visualization": visualization.model_dump(mode="json"),
            "columns": list(attempt.execution.preview.columns),
            "row_count": visualization.data_source.row_count,
        }

    def _candidate_from_arguments(
        self,
        *,
        arguments: dict[str, Any],
        attempt: SqlExecutionAttempt,
    ) -> VisualizationSpec | None:
        kind = self._required_str(arguments.get("kind"))
        x = self._required_str(arguments.get("x"))
        y = self._string_list(arguments.get("y"))
        why_kind = self._required_str(arguments.get("why_kind")) or ""
        if kind is None or x is None or not y:
            return None

        try:
            return VisualizationSpec(
                version="v2",
                kind=kind,
                goal=self._str_or_default(arguments.get("goal"), "inspection"),
                title=self._optional_str(arguments.get("title")),
                summary=self._optional_str(arguments.get("summary")),
                data_source=VisualizationDataSource(
                    execution_id=attempt.execution.execution_id,
                    row_count=attempt.execution.row_count
                    if attempt.execution.row_count is not None
                    else len(attempt.execution.preview.rows),
                    is_truncated=bool("preview_truncated" in attempt.execution.warnings),
                ),
                encoding=VisualizationEncoding(
                    x=x,
                    y=y,
                    series=self._optional_str(arguments.get("series")),
                    color=self._optional_str(arguments.get("color")),
                    size=self._optional_str(arguments.get("size")),
                    tooltip=self._string_list(arguments.get("tooltip")),
                    secondary_y=self._string_list(arguments.get("secondary_y")),
                ),
                transform=VisualizationTransform(
                    sort=self._optional_str(arguments.get("sort")),
                    limit=self._int_or_default(arguments.get("limit"), 50),
                    aggregation=self._str_or_default(arguments.get("aggregation"), "none"),
                    top_n=self._optional_int(arguments.get("top_n")),
                    normalize=self._str_or_default(arguments.get("normalize"), "none"),
                    cumulative=bool(arguments.get("cumulative", False)),
                ),
                rationale=VisualizationRationale(
                    why_kind=why_kind,
                    why_fields=self._string_list(arguments.get("why_fields")),
                ),
            )
        except Exception:
            return None

    @staticmethod
    def _execution_attempt_by_id(
        *,
        state: ToolOrchestrationState,
        execution_id: str,
    ) -> SqlExecutionAttempt | None:
        for attempt in reversed(state.execution_attempts):
            if (
                attempt.execution.execution_id == execution_id
                and attempt.execution.status == "succeeded"
            ):
                return attempt
        return None

    @staticmethod
    def _required_str(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @classmethod
    def _optional_str(cls, value: Any) -> str | None:
        return cls._required_str(value)

    @classmethod
    def _str_or_default(cls, value: Any, default: str) -> str:
        return cls._required_str(value) or default

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        return None

    @classmethod
    def _int_or_default(cls, value: Any, default: int) -> int:
        return cls._optional_int(value) or default

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
