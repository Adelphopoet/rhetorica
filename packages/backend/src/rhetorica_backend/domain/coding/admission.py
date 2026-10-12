"""Feature admission independent from transports and orchestration."""

from __future__ import annotations

from rhetorica_backend.contracts.coding import CodingReasonCode


class CodingAdmissionError(RuntimeError):
    def __init__(self, reason_code: CodingReasonCode):
        self.reason_code = reason_code
        super().__init__(reason_code.value)


class CodingAdmissionPolicy:
    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled

    def require_new_run_enabled(self) -> None:
        if not self._enabled:
            raise CodingAdmissionError(CodingReasonCode.CODING_DISABLED)
