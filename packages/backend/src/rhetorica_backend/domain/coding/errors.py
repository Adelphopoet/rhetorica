"""Stable errors for durable coding control-plane operations."""

from __future__ import annotations


class CodingRunError(ValueError):
    def __init__(self, code: str, *, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class CodingRunConflict(CodingRunError):
    def __init__(self, code: str = "coding_run_conflict") -> None:
        super().__init__(code, status_code=409)


class CodingRunNotFound(CodingRunError):
    def __init__(self, code: str = "coding_run_not_found") -> None:
        super().__init__(code, status_code=404)


class CodingTaskConflict(CodingRunError):
    def __init__(self, code: str = "coding_task_conflict") -> None:
        super().__init__(code, status_code=409)


class CodingDelegationDenied(CodingRunError):
    def __init__(self, code: str = "coding_delegation_denied") -> None:
        super().__init__(code, status_code=403)


class CodingBudgetExhausted(CodingRunError):
    def __init__(self, code: str = "coding_iteration_limit_reached") -> None:
        super().__init__(code, status_code=409)


class CodingToolAccessDenied(CodingRunError):
    def __init__(self, code: str = "coding_tool_access_denied") -> None:
        super().__init__(code, status_code=403)


class CodingEnvironmentPreparationFailed(CodingRunError):
    def __init__(self) -> None:
        super().__init__("coding_dependency_install_failed")


class CodingTeamValidationError(CodingRunError):
    """A team reference is unusable; names the member so an administrator can act."""

    def __init__(
        self,
        code: str,
        *,
        member_id: str | None = None,
        member_label: str | None = None,
        status_code: int = 422,
    ) -> None:
        super().__init__(code, status_code=status_code)
        self.member_id = member_id
        self.member_label = member_label

    @property
    def detail(self) -> dict[str, str]:
        detail = {"code": self.code}
        if self.member_id is not None:
            detail["member_id"] = self.member_id
        if self.member_label is not None:
            detail["member_label"] = self.member_label
        return detail
