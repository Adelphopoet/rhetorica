"""The public execution journal shared by live delivery and persisted history."""

from rhetorica_backend.contracts.chat import StreamEvent


class ChatJournal:
    """Retain public progress only; answer chunks and terminal payloads live separately."""

    _TYPES = frozenset({"thinking", "tool_call_start", "tool_call_end", "execution_status"})

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def record(self, event: StreamEvent) -> None:
        if event.type.value in self._TYPES:
            self.events.append(event.model_dump(mode="json"))
