"""One discussion budget includes every model call, including compaction."""

from collections.abc import Awaitable, Callable

from rhetorica_backend.domain.coding.errors import CodingRunError

from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage


class CodingPlanningModel(Model):
    def __init__(
        self,
        delegate: Model,
        *,
        max_calls: int,
        on_usage: Callable[[ModelResponse], Awaitable[None]] | None = None,
    ):
        self._delegate, self._max_calls = delegate, max_calls
        self._calls = 0
        self._on_usage = on_usage

    async def get_response(self, *args, **kwargs):
        if self._calls >= self._max_calls:
            raise CodingRunError("coding_planning_iteration_limit_reached")
        self._calls += 1
        response = await self._delegate.get_response(*args, **kwargs)
        if self._on_usage is not None:
            await self._on_usage(response)
        return response

    async def stream_response(self, *args, **kwargs):
        if self._calls >= self._max_calls:
            raise CodingRunError("coding_planning_iteration_limit_reached")
        self._calls += 1
        accounted = False
        async for event in self._delegate.stream_response(*args, **kwargs):
            if (
                self._on_usage is not None
                and not accounted
                and getattr(event, "type", None)
                in {
                    "response.completed",
                    "response.incomplete",
                    "response.failed",
                }
            ):
                response = event.response
                if response.usage is not None:
                    raw = response.usage
                    await self._on_usage(
                        ModelResponse(
                            output=[],
                            response_id=response.id,
                            usage=Usage(
                                requests=1,
                                input_tokens=raw.input_tokens,
                                output_tokens=raw.output_tokens,
                                total_tokens=raw.total_tokens,
                            ),
                            raw_usage=raw.model_dump(exclude_unset=True),
                        )
                    )
                    accounted = True
            yield event
