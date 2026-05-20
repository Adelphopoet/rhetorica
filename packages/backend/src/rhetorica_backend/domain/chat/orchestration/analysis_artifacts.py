"""Typed runtime artifacts for agentic analytics orchestration."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

AnalysisPlanStepStatus = Literal[
    "planned",
    "running",
    "done",
    "blocked",
    "failed",
    "cancelled",
]
AnalysisPlanStatus = Literal[
    "planned",
    "running",
    "blocked",
    "completed",
    "failed",
    "cancelled",
]
AnalysisRunStatus = Literal[
    "planned",
    "running",
    "blocked",
    "awaiting_user",
    "completed",
    "failed",
    "cancelled",
]
AnalysisWorkerStatus = Literal["succeeded", "failed", "blocked", "cancelled"]
AnalysisEvidenceType = Literal[
    "metadata_entity",
    "execution_result",
    "table_proof",
    "final_answer",
]

SUPPORTED_ANALYSIS_EVIDENCE_TYPES: frozenset[AnalysisEvidenceType] = frozenset({
    "metadata_entity",
    "execution_result",
    "table_proof",
    "final_answer",
})


class AnalysisPlanStep(BaseModel):
    """One explicit analyst-visible step in a multi-step investigation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    status: AnalysisPlanStepStatus = "planned"
    required_evidence: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    blocker: str | None = None


class AnalysisPlan(BaseModel):
    """Durable compact plan for root-cause and decomposition workflows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=500)
    status: AnalysisPlanStatus = "planned"
    steps: list[AnalysisPlanStep] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def compact_payload(self) -> dict[str, Any]:
        """Return only prompt-safe plan fields."""
        return {
            "goal": self.goal,
            "status": self.status,
            "steps": [step.model_dump(mode="json") for step in self.steps],
            "required_evidence": list(self.required_evidence),
            "open_questions": list(self.open_questions),
            "warnings": list(self.warnings),
        }


class AnalysisPlanLifecycle:
    """Pure lifecycle operations for immutable analysis plans."""

    def create(
        self,
        *,
        plan_id: str,
        goal: str,
        steps: Iterable[AnalysisPlanStep],
        required_evidence: Iterable[str] = (),
    ) -> AnalysisPlan:
        return AnalysisPlan(
            plan_id=plan_id,
            goal=goal,
            status="planned",
            steps=list(steps),
            required_evidence=list(required_evidence),
        )

    def update_step(
        self,
        *,
        plan: AnalysisPlan,
        step_id: str,
        status: AnalysisPlanStepStatus,
        evidence_ids: Iterable[str] = (),
        blocker: str | None = None,
    ) -> AnalysisPlan:
        steps: list[AnalysisPlanStep] = []
        matched = False
        for step in plan.steps:
            if step.id != step_id:
                steps.append(step)
                continue
            matched = True
            merged_evidence = self._merge_unique(step.evidence_ids, evidence_ids)
            steps.append(
                step.model_copy(
                    update={
                        "status": status,
                        "evidence_ids": merged_evidence,
                        "blocker": blocker,
                    }
                )
            )
        warnings = list(plan.warnings)
        if not matched:
            warnings.append(f"unknown_plan_step:{step_id}")
        return plan.model_copy(
            update={
                "steps": steps,
                "status": self._resolve_status(steps, default=plan.status),
                "warnings": warnings,
            }
        )

    def is_complete(self, plan: AnalysisPlan) -> bool:
        if plan.status != "completed":
            return False
        if any(step.status != "done" for step in plan.steps):
            return False
        evidence_ids = self._plan_evidence_ids(plan)
        return all(evidence_id in evidence_ids for evidence_id in plan.required_evidence)

    @staticmethod
    def _merge_unique(existing: Iterable[str], incoming: Iterable[str]) -> list[str]:
        merged: list[str] = []
        for item in [*existing, *incoming]:
            if item and item not in merged:
                merged.append(item)
        return merged

    @staticmethod
    def _plan_evidence_ids(plan: AnalysisPlan) -> set[str]:
        return {
            evidence_id
            for step in plan.steps
            for evidence_id in step.evidence_ids
            if evidence_id
        }

    @staticmethod
    def _resolve_status(
        steps: list[AnalysisPlanStep],
        *,
        default: AnalysisPlanStatus,
    ) -> AnalysisPlanStatus:
        if not steps:
            return default
        statuses = {step.status for step in steps}
        if "failed" in statuses:
            return "failed"
        if "cancelled" in statuses:
            return "cancelled"
        if "blocked" in statuses:
            return "blocked"
        if all(status == "done" for status in statuses):
            return "completed"
        if "running" in statuses or "done" in statuses:
            return "running"
        return "planned"


class AnalysisPlanCompletionGuard:
    """Blocks final answers when an active plan still misses required evidence."""

    def can_finalize(self, plan: AnalysisPlan | None) -> bool:
        if plan is None:
            return True
        if plan.status in {"blocked", "failed", "cancelled"}:
            return False
        if self.incomplete_steps(plan):
            return False
        return not self.missing_evidence(plan)

    def incomplete_steps(self, plan: AnalysisPlan | None) -> list[str]:
        if plan is None:
            return []
        return [step.id for step in plan.steps if step.status != "done"]

    def missing_evidence(self, plan: AnalysisPlan | None) -> list[str]:
        if plan is None:
            return []
        present = AnalysisPlanLifecycle._plan_evidence_ids(plan)
        return [
            evidence_id
            for evidence_id in plan.required_evidence
            if evidence_id not in present
        ]


class WorkerContextBudget(BaseModel):
    """Context budget handed to an isolated worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_messages: int = Field(default=8, ge=1, le=50)
    max_prompt_tokens: int = Field(default=4_000, ge=512)
    max_tool_calls: int = Field(default=3, ge=0, le=20)
    max_rows: int = Field(default=500, ge=0, le=5_000)


class AnalysisWorkerTask(BaseModel):
    """Scoped read-only worker task created from an analysis plan step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    step_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=500)
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_tables: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    budget: WorkerContextBudget = Field(default_factory=WorkerContextBudget)


class AnalysisWorkerResult(BaseModel):
    """Compact worker output merged into the parent context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=500)
    status: AnalysisWorkerStatus
    key_finding: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    blocker: str | None = None
    next_action: str | None = None
    warnings: list[str] = Field(default_factory=list)
    execution_outcome: dict[str, Any] | None = None

    def compact_payload(self) -> dict[str, Any]:
        payload = self.model_dump(
            mode="json",
            exclude_none=True,
            exclude={"execution_outcome"},
        )
        # Include 'ok' field for compatibility with tools that expect it.
        payload["ok"] = self.status == "succeeded"
        return payload


class AnalysisCheckpoint(BaseModel):
    """Conversation-level durable checkpoint for an active analysis run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1, max_length=128)
    plan_id: str | None = None
    status: AnalysisRunStatus = "planned"
    active_step_id: str | None = None
    completed_step_ids: list[str] = Field(default_factory=list)
    blocked_step_id: str | None = None
    budget_state: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    blocker: str | None = None
    next_action: str | None = None
    warnings: list[str] = Field(default_factory=list)

    def digest_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class AnalysisCheckpointLifecycle:
    """Pure checkpoint state transitions."""

    def start(self, *, run_id: str, plan_id: str | None = None) -> AnalysisCheckpoint:
        return AnalysisCheckpoint(run_id=run_id, plan_id=plan_id, status="planned")

    def mark_step_running(
        self,
        *,
        checkpoint: AnalysisCheckpoint,
        step_id: str,
    ) -> AnalysisCheckpoint:
        return checkpoint.model_copy(
            update={
                "status": "running",
                "active_step_id": step_id,
                "blocked_step_id": None,
                "blocker": None,
            }
        )

    def mark_step_completed(
        self,
        *,
        checkpoint: AnalysisCheckpoint,
        step_id: str,
        evidence_ids: Iterable[str] = (),
    ) -> AnalysisCheckpoint:
        completed = AnalysisPlanLifecycle._merge_unique(
            checkpoint.completed_step_ids,
            [step_id],
        )
        evidence = AnalysisPlanLifecycle._merge_unique(
            checkpoint.evidence_ids,
            evidence_ids,
        )
        return checkpoint.model_copy(
            update={
                "status": "running",
                "active_step_id": None,
                "completed_step_ids": completed,
                "evidence_ids": evidence,
            }
        )

    def block(
        self,
        *,
        checkpoint: AnalysisCheckpoint,
        step_id: str,
        blocker: str,
        awaiting_user: bool = False,
    ) -> AnalysisCheckpoint:
        return checkpoint.model_copy(
            update={
                "status": "awaiting_user" if awaiting_user else "blocked",
                "active_step_id": None,
                "blocked_step_id": step_id,
                "blocker": blocker,
            }
        )

    def require_approval(
        self,
        *,
        checkpoint: AnalysisCheckpoint,
        step_id: str,
        blocker: str,
        approval: dict[str, Any],
    ) -> AnalysisCheckpoint:
        budget_state = dict(checkpoint.budget_state)
        budget_state["pending_approval"] = approval
        budget_state.pop("approved_approval_token", None)
        budget_state.pop("consumed_approval_token", None)
        return checkpoint.model_copy(
            update={
                "status": "awaiting_user",
                "active_step_id": None,
                "blocked_step_id": step_id,
                "blocker": blocker,
                "next_action": "approve_sql_execution",
                "budget_state": budget_state,
            }
        )

    def approve(self, *, checkpoint: AnalysisCheckpoint) -> AnalysisCheckpoint:
        pending = checkpoint.budget_state.get("pending_approval")
        if not isinstance(pending, dict):
            return checkpoint
        token = pending.get("approval_token")
        budget_state = dict(checkpoint.budget_state)
        if isinstance(token, str) and token.strip():
            budget_state["approved_approval_token"] = token.strip()
        budget_state.pop("pending_approval", None)
        return checkpoint.model_copy(
            update={
                "status": "running",
                "active_step_id": checkpoint.blocked_step_id or checkpoint.active_step_id,
                "blocked_step_id": None,
                "blocker": None,
                "next_action": None,
                "budget_state": budget_state,
            }
        )

    def consume_approval(
        self,
        *,
        checkpoint: AnalysisCheckpoint,
        approval_token: str,
    ) -> AnalysisCheckpoint:
        budget_state = dict(checkpoint.budget_state)
        budget_state.pop("approved_approval_token", None)
        budget_state["consumed_approval_token"] = approval_token
        return checkpoint.model_copy(update={"budget_state": budget_state})

    def cancel(self, *, checkpoint: AnalysisCheckpoint) -> AnalysisCheckpoint:
        return checkpoint.model_copy(
            update={
                "status": "cancelled",
                "active_step_id": None,
            }
        )

    def complete(self, *, checkpoint: AnalysisCheckpoint) -> AnalysisCheckpoint:
        return checkpoint.model_copy(
            update={
                "status": "completed",
                "active_step_id": None,
                "blocked_step_id": None,
                "blocker": None,
            }
        )

    def fail(
        self,
        *,
        checkpoint: AnalysisCheckpoint,
        step_id: str | None,
        blocker: str,
    ) -> AnalysisCheckpoint:
        return checkpoint.model_copy(
            update={
                "status": "failed",
                "active_step_id": None,
                "blocked_step_id": step_id,
                "blocker": blocker,
            }
        )


class AnalysisPlanDraftStep(BaseModel):
    """One LLM-proposed plan step before runtime state is created."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=255)
    required_evidence: list[str] = Field(min_length=1, max_length=8)


class AnalysisPlanDraft(BaseModel):
    """LLM-proposed plan payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(min_length=1, max_length=500)
    steps: list[AnalysisPlanDraftStep] = Field(min_length=1, max_length=6)
    required_evidence: list[str] = Field(default_factory=list, max_length=12)
    open_questions: list[str] = Field(default_factory=list, max_length=6)


class AnalysisPlanDraftDecision(BaseModel):
    """Structured LLM decision for whether the turn needs an analysis plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    should_plan: bool
    plan: AnalysisPlanDraft | None
    warnings: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _validate_plan_presence(self) -> AnalysisPlanDraftDecision:
        if self.should_plan and self.plan is None:
            raise ValueError("plan is required when should_plan is true")
        if not self.should_plan and self.plan is not None:
            raise ValueError("plan must be null when should_plan is false")
        return self


class AnalysisPlanDraftValidator:
    """Validates LLM-proposed plan contracts before they become runtime state."""

    def validate(self, draft: AnalysisPlanDraft) -> AnalysisPlanDraft:
        self._ensure_unique_step_ids(draft.steps)
        self._ensure_required_evidence_is_supported(draft)
        self._ensure_required_evidence_is_reachable(draft)
        return draft

    @staticmethod
    def _ensure_unique_step_ids(steps: Iterable[AnalysisPlanDraftStep]) -> None:
        seen: set[str] = set()
        for step in steps:
            if step.id in seen:
                raise ValueError(f"duplicate plan step id: {step.id}")
            seen.add(step.id)

    @staticmethod
    def _ensure_required_evidence_is_reachable(draft: AnalysisPlanDraft) -> None:
        if not draft.required_evidence:
            return
        step_evidence = {
            evidence_id
            for step in draft.steps
            for evidence_id in step.required_evidence
            if evidence_id
        }
        missing = [
            evidence_id
            for evidence_id in draft.required_evidence
            if evidence_id not in step_evidence
        ]
        if missing:
            raise ValueError("unreachable required evidence: " + ",".join(missing))

    @staticmethod
    def _ensure_required_evidence_is_supported(draft: AnalysisPlanDraft) -> None:
        evidence_ids = [
            *draft.required_evidence,
            *[
                evidence_id
                for step in draft.steps
                for evidence_id in step.required_evidence
            ],
        ]
        unsupported = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id not in SUPPORTED_ANALYSIS_EVIDENCE_TYPES
        ]
        if unsupported:
            raise ValueError("unsupported required evidence: " + ",".join(unsupported))


@dataclass(frozen=True)
class StructuredJsonPayload:
    """JSON payload extracted from an LLM response plus protocol warnings."""

    content: str
    warnings: tuple[str, ...] = ()


class StructuredJsonResponseExtractor:
    """Extracts schema JSON from explicit provider response envelopes."""

    _THINK_START = "<think>"
    _THINK_END = "</think>"

    def extract(self, content: str) -> StructuredJsonPayload:
        normalized = content.strip()
        if normalized.startswith("{"):
            return StructuredJsonPayload(content=normalized)
        if not normalized.startswith(self._THINK_START):
            return StructuredJsonPayload(content=normalized)
        end_index = normalized.find(self._THINK_END)
        if end_index < 0:
            return StructuredJsonPayload(content=normalized)
        candidate = normalized[end_index + len(self._THINK_END) :].strip()
        if not candidate.startswith("{"):
            return StructuredJsonPayload(content=normalized)
        return StructuredJsonPayload(
            content=candidate,
            warnings=("analysis_planner_reasoning_envelope_stripped",),
        )


class AnalysisPlanBuilder:
    """Builds immutable runtime plans from validated LLM drafts."""

    def build(self, *, draft: AnalysisPlanDraft, warnings: Iterable[str] = ()) -> AnalysisPlan:
        plan = AnalysisPlanLifecycle().create(
            plan_id=f"plan-{uuid4()}",
            goal=draft.goal,
            steps=[
                AnalysisPlanStep(
                    id=step.id,
                    title=step.title,
                    required_evidence=list(step.required_evidence),
                )
                for step in draft.steps
            ],
            required_evidence=list(draft.required_evidence),
        )
        return plan.model_copy(update={"open_questions": list(draft.open_questions), "warnings": list(warnings)})


@dataclass(frozen=True)
class AnalysisPlanningResult:
    """Outcome of the LLM planning phase."""

    plan: AnalysisPlan | None
    warnings: tuple[str, ...] = ()


class AnalysisPlannerPromptBuilder:
    """Builds the isolated planner prompt."""

    @staticmethod
    def system_prompt() -> str:
        return (
            "You are the planning phase for governed analytics orchestration.\n"
            "Return only JSON matching the provided schema.\n"
            "The first character must be { and the last character must be }.\n"
            "Do not output <think>, reasoning, markdown, prose, or code fences.\n"
            "Create a visible multi-step analysis plan when the analyst asks for proof, tables, SQL-backed comparison, metric validation, root cause analysis, or any multi-step data investigation.\n"
            "If it does, create specific analyst-visible steps that can be closed by evidence.\n"
            "Use stable ASCII step ids in snake_case or kebab-case.\n"
            "Every step must have at least one required_evidence item.\n"
            "Every step required_evidence and every top-level required_evidence value must be one of: metadata_entity, execution_result, table_proof, final_answer.\n"
            "Use metadata_entity for metadata/catalog lookup steps.\n"
            "Use execution_result for SQL execution steps.\n"
            "Use table_proof for result tables that prove the answer.\n"
            "Use final_answer for final presentation/answer delivery steps.\n"
            "Do not invent concrete evidence ids before tools run.\n"
            "Warnings must be stable machine-readable codes only.\n"
            "Do not call tools. Do not include raw SQL. Do not describe internal implementation."
        )

    @staticmethod
    def user_prompt(
        *,
        question: str,
        available_tools: Iterable[str],
        context_tables: Iterable[str],
        metadata_sources: Iterable[str],
    ) -> str:
        return json.dumps(
            {
                "question": question,
                "available_tools": list(available_tools),
                "context_tables": list(context_tables),
                "metadata_sources": list(metadata_sources),
            },
            ensure_ascii=False,
            sort_keys=True,
        )


class LlmAnalysisPlanner:
    """Runs the LLM planning phase and returns validated runtime artifacts."""

    def __init__(
        self,
        *,
        prompt_builder: AnalysisPlannerPromptBuilder | None = None,
        validator: AnalysisPlanDraftValidator | None = None,
        plan_builder: AnalysisPlanBuilder | None = None,
        response_extractor: StructuredJsonResponseExtractor | None = None,
    ) -> None:
        self._prompt_builder = prompt_builder or AnalysisPlannerPromptBuilder()
        self._validator = validator or AnalysisPlanDraftValidator()
        self._plan_builder = plan_builder or AnalysisPlanBuilder()
        self._response_extractor = response_extractor or StructuredJsonResponseExtractor()

    def plan(
        self,
        *,
        llm_client: Any,
        question: str,
        available_tools: Iterable[str],
        context_tables: Iterable[str] = (),
        metadata_sources: Iterable[str] = (),
    ) -> AnalysisPlanningResult:
        try:
            response, _tools_disabled, structured_disabled = llm_client.chat_completion_with_fallback(
                messages=[
                    {"role": "system", "content": self._prompt_builder.system_prompt()},
                    {
                        "role": "user",
                        "content": self._prompt_builder.user_prompt(
                            question=question,
                            available_tools=available_tools,
                            context_tables=context_tables,
                            metadata_sources=metadata_sources,
                        ),
                    },
                ],
                tools=[],
                response_schema=analysis_plan_decision_json_schema(),
            )
        except Exception:
            return AnalysisPlanningResult(plan=None, warnings=("analysis_planner_failed",))
        if structured_disabled:
            return AnalysisPlanningResult(
                plan=None,
                warnings=("analysis_planner_structured_output_not_supported",),
            )
        extracted = self._response_extractor.extract(self._message_content(response.message))
        try:
            decision = AnalysisPlanDraftDecision.model_validate_json(extracted.content)
        except Exception:
            return AnalysisPlanningResult(
                plan=None,
                warnings=(
                    "analysis_planner_invalid_response",
                    *extracted.warnings,
                ),
            )
        if not decision.should_plan:
            return AnalysisPlanningResult(
                plan=None,
                warnings=(*extracted.warnings, *tuple(decision.warnings)),
            )
        if decision.plan is None:
            return AnalysisPlanningResult(
                plan=None,
                warnings=(
                    "analysis_planner_missing_plan",
                    *extracted.warnings,
                    *tuple(decision.warnings),
                ),
            )
        try:
            draft = self._validator.validate(decision.plan)
        except Exception:
            return AnalysisPlanningResult(
                plan=None,
                warnings=(
                    "analysis_planner_contract_violation",
                    *extracted.warnings,
                    *tuple(decision.warnings),
                ),
            )
        plan = self._plan_builder.build(draft=draft, warnings=decision.warnings)
        return AnalysisPlanningResult(
            plan=plan,
            warnings=(*extracted.warnings, *tuple(decision.warnings)),
        )

    @staticmethod
    def _message_content(message: dict[str, Any]) -> str:
        content = message.get("content")
        return content if isinstance(content, str) else ""


def analysis_plan_decision_json_schema() -> dict[str, Any]:
    """Strict JSON schema for the LLM planning phase."""

    required_evidence_item_schema = {
        "type": "string",
        "enum": sorted(SUPPORTED_ANALYSIS_EVIDENCE_TYPES),
    }
    step_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "title", "required_evidence"],
        "properties": {
            "id": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[A-Za-z0-9_-]+$"},
            "title": {"type": "string", "minLength": 1, "maxLength": 255},
            "required_evidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": required_evidence_item_schema,
            },
        },
    }
    plan_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["goal", "steps", "required_evidence", "open_questions"],
        "properties": {
            "goal": {"type": "string", "minLength": 1, "maxLength": 500},
            "steps": {"type": "array", "minItems": 1, "maxItems": 6, "items": step_schema},
            "required_evidence": {
                "type": "array",
                "maxItems": 12,
                "items": required_evidence_item_schema,
            },
            "open_questions": {
                "type": "array",
                "maxItems": 6,
                "items": {"type": "string", "minLength": 1, "maxLength": 255},
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["should_plan", "plan", "warnings"],
        "properties": {
            "should_plan": {"type": "boolean"},
            "plan": {"anyOf": [plan_schema, {"type": "null"}]},
            "warnings": {
                "type": "array",
                "maxItems": 8,
                "items": {"type": "string", "minLength": 1, "maxLength": 128},
            },
        },
    }


class AnalysisRuntimeProgressService:
    """Maps runtime tool progress into plan/checkpoint transitions."""

    _METADATA_EVIDENCE_TYPE = "metadata_entity"
    _EXECUTION_EVIDENCE_TYPE = "execution_result"
    _TABLE_PROOF_EVIDENCE_TYPE = "table_proof"
    _TOOL_EVIDENCE_TYPES: ClassVar[dict[str, tuple[str, ...]]] = {
        "fetch_metadata_tables": (_METADATA_EVIDENCE_TYPE,),
        "search_metadata_catalog": (_METADATA_EVIDENCE_TYPE,),
        "get_metadata_entity_details": (_METADATA_EVIDENCE_TYPE,),
        "get_metadata_entity_lineage": (_METADATA_EVIDENCE_TYPE,),
        "run_sql": (_EXECUTION_EVIDENCE_TYPE, _TABLE_PROOF_EVIDENCE_TYPE),
    }

    def start_next_step(self, *, state: Any, tool_name: str) -> None:
        evidence_types = self._TOOL_EVIDENCE_TYPES.get(tool_name)
        if not evidence_types:
            return
        plan = getattr(state, "analysis_plan", None)
        if plan is None:
            return
        step = self._next_open_step(plan, evidence_types=evidence_types)
        if step is None:
            return
        lifecycle = AnalysisPlanLifecycle()
        state.analysis_plan = lifecycle.update_step(
            plan=plan,
            step_id=step.id,
            status="running",
        )
        checkpoint = getattr(state, "checkpoint", None)
        if checkpoint is not None:
            state.checkpoint = AnalysisCheckpointLifecycle().mark_step_running(
                checkpoint=checkpoint,
                step_id=step.id,
            )
        self._persist(state)

    def complete_current_step(
        self,
        *,
        state: Any,
        tool_name: str,
        tool_result: dict[str, Any],
    ) -> None:
        evidence_types = self._TOOL_EVIDENCE_TYPES.get(tool_name)
        if not evidence_types:
            return
        plan = getattr(state, "analysis_plan", None)
        if plan is None:
            return
        step = self._running_step(plan) or self._next_open_step(
            plan,
            evidence_types=evidence_types,
        )
        if step is None:
            return
        evidence_ids = self._evidence_ids_for_result(
            tool_name=tool_name,
            tool_result=tool_result,
        )
        approval_required = tool_result.get("error_code") == "sql_approval_required"
        status: AnalysisPlanStepStatus
        if tool_result.get("ok") is True:
            status = "done"
        elif approval_required:
            status = "blocked"
        else:
            status = "failed"
        blocker = None if status == "done" else self._tool_error(tool_result)
        lifecycle = AnalysisPlanLifecycle()
        state.analysis_plan = lifecycle.update_step(
            plan=plan,
            step_id=step.id,
            status=status,
            evidence_ids=evidence_ids,
            blocker=blocker,
        )
        checkpoint = getattr(state, "checkpoint", None)
        if checkpoint is None:
            return
        checkpoint_lifecycle = AnalysisCheckpointLifecycle()
        if status == "done":
            checkpoint = checkpoint_lifecycle.mark_step_completed(
                checkpoint=checkpoint,
                step_id=step.id,
                evidence_ids=evidence_ids,
            )
            if AnalysisPlanLifecycle().is_complete(state.analysis_plan):
                checkpoint = checkpoint_lifecycle.complete(checkpoint=checkpoint)
        elif approval_required:
            approval_payload = tool_result.get("approval")
            checkpoint = checkpoint_lifecycle.require_approval(
                checkpoint=checkpoint,
                step_id=step.id,
                blocker=blocker or "sql_approval_required",
                approval=approval_payload if isinstance(approval_payload, dict) else {},
            )
        else:
            checkpoint = checkpoint_lifecycle.fail(
                checkpoint=checkpoint,
                step_id=step.id,
                blocker=blocker or "tool_failed",
            )
        state.checkpoint = checkpoint
        self._persist(state)

    @staticmethod
    def _next_open_step(
        plan: AnalysisPlan,
        *,
        evidence_types: Iterable[str],
    ) -> AnalysisPlanStep | None:
        evidence_type_set = set(evidence_types)
        for step in plan.steps:
            if step.status not in {"planned", "blocked"}:
                continue
            if evidence_type_set.intersection(step.required_evidence):
                return step
        return None

    @staticmethod
    def _running_step(plan: AnalysisPlan) -> AnalysisPlanStep | None:
        for step in plan.steps:
            if step.status == "running":
                return step
        return None

    @classmethod
    def _evidence_ids_for_result(
        cls,
        *,
        tool_name: str,
        tool_result: dict[str, Any],
    ) -> list[str]:
        if tool_result.get("ok") is not True:
            return []
        if tool_name in {
            "fetch_metadata_tables",
            "search_metadata_catalog",
            "get_metadata_entity_details",
            "get_metadata_entity_lineage",
        }:
            return [cls._METADATA_EVIDENCE_TYPE]
        if tool_name != "run_sql":
            return []
        evidence_ids = [cls._EXECUTION_EVIDENCE_TYPE]
        execution = tool_result.get("execution")
        execution_id = None
        if isinstance(execution, dict):
            execution_id = execution.get("execution_id")
        if not isinstance(execution_id, str):
            execution_id = tool_result.get("execution_id")
        if isinstance(execution_id, str) and execution_id.strip():
            evidence_ids.append(execution_id.strip())
        if cls._has_result_table(tool_result):
            evidence_ids.append(cls._TABLE_PROOF_EVIDENCE_TYPE)
        return evidence_ids

    @staticmethod
    def _has_result_table(tool_result: dict[str, Any]) -> bool:
        execution = tool_result.get("execution")
        preview = execution.get("preview") if isinstance(execution, dict) else None
        if isinstance(preview, dict) and preview.get("columns") and preview.get("rows"):
            return True
        return bool(tool_result.get("columns") and tool_result.get("rows"))

    @staticmethod
    def _tool_error(tool_result: dict[str, Any]) -> str:
        for key in ("error_code", "error", "message", "user_message"):
            value = tool_result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "tool_failed"

    @staticmethod
    def _persist(state: Any) -> None:
        callback = getattr(state, "checkpoint_persist", None)
        if callable(callback):
            callback(state)
