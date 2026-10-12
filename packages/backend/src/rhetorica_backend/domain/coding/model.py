"""Reserve a durable iteration immediately before each SDK model request."""

import asyncio
import hashlib
import json
from dataclasses import asdict, is_dataclass
from enum import Enum

from pydantic import BaseModel

from agents import FunctionTool
from agents.agent_output import AgentOutputSchemaBase
from agents.models.interface import Model


def _request_value(value):
    """Stable request identity excludes in-process callbacks and memory addresses."""
    if isinstance(value, FunctionTool):
        return {
            "name": value.name,
            "description": value.description,
            "schema": value.params_json_schema,
            "strict": value.strict_json_schema,
        }
    if isinstance(value, AgentOutputSchemaBase):
        return None if value.is_plain_text() else value.json_schema()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    raise TypeError("coding_model_input_not_serializable")


class CodingBudgetModel(Model):
    def __init__(
        self,
        delegate: Model,
        *,
        runs,
        task,
        operation_id,
        deadline,
        journal=None,
        usage_format="openai",
        model_name=None,
    ):
        self._delegate = delegate
        self._runs = runs
        self._task = task
        self._operation_id = operation_id
        self._deadline = deadline
        self._calls = 0
        self._journal = journal
        self._usage_format = usage_format
        self._model_name = model_name

    async def get_response(self, *args, **kwargs):
        fingerprint = hashlib.sha256(
            json.dumps([args, kwargs], sort_keys=True, default=_request_value).encode()
        ).hexdigest()
        invocation = (
            f"{self._task.run_id}:{self._task.id}:{self._operation_id}"
            if self._calls == 0
            else "coding-model:"
            + hashlib.sha256(
                f"{self._task.run_id}:{self._task.id}:{self._operation_id}:{fingerprint}".encode()
            ).hexdigest()
        )
        self._calls += 1
        key = f"model:{self._operation_id}:{fingerprint}"
        scope = {"run_id": self._task.run_id, "task_id": self._task.id, "operation_id": key}
        from pydantic import TypeAdapter

        from agents.items import ModelResponse

        adapter = TypeAdapter(ModelResponse)
        if self._journal:
            cached = await asyncio.to_thread(self._journal.get, **scope)
            if cached is not None:
                response = adapter.validate_python(cached)
                await self._record_usage(response, invocation, args, kwargs)
                return response
        reservation = await asyncio.to_thread(
            self._runs.reserve_iteration,
            run_id=self._task.run_id,
            task_id=self._task.id,
            executing_agent_profile_id=self._task.agent_profile_id,
            invocation_id=invocation,
            workspace_id=self._task.workspace_id,
            owner_user_id=self._task.owner_user_id,
        )
        from datetime import UTC, datetime

        remaining = (
            min(self._deadline.expires_at, reservation.deadline) - datetime.now(UTC)
        ).total_seconds()
        from rhetorica_backend.domain.coding.errors import CodingBudgetExhausted

        if remaining <= 0:
            raise CodingBudgetExhausted("coding_runtime_limit_reached")
        try:
            async with asyncio.timeout(remaining):
                response = await self._delegate.get_response(*args, **kwargs)
                if self._journal:
                    await asyncio.to_thread(
                        self._journal.put,
                        **scope,
                        value=adapter.dump_python(response, mode="json", exclude_unset=True),
                    )
                await self._record_usage(response, invocation, args, kwargs)
                return response
        except TimeoutError as exc:
            raise CodingBudgetExhausted("coding_runtime_limit_reached") from exc

    async def _record_usage(self, response, invocation, args, kwargs):
        from rhetorica_backend.domain.chat.llm.token_budget import LlmTokenEstimator
        from rhetorica_backend.domain.coding.usage import model_usage

        serialized = json.dumps(
            [args, kwargs], ensure_ascii=False, default=_request_value, separators=(",", ":")
        )
        usage = {
            **model_usage(response, usage_format=self._usage_format),
            "model": self._model_name,
            "request_chars": len(serialized),
            "estimated_request_tokens": LlmTokenEstimator(self._model_name).count_text(serialized),
        }
        await asyncio.to_thread(
            self._runs.record_model_usage,
            run_id=self._task.run_id,
            task_id=self._task.id,
            invocation_id=invocation,
            workspace_id=self._task.workspace_id,
            owner_user_id=self._task.owner_user_id,
            usage=usage,
        )

    async def stream_response(self, *args, **kwargs):
        raise RuntimeError("coding_model_stream_not_supported")
        yield
