"""Stable scenario domain errors shared by HTTP and activity adapters."""

from __future__ import annotations


class WorkflowError(ValueError):
    def __init__(self, code: str, message: str | None = None, *, status_code: int = 400):
        self.code = code
        self.status_code = status_code
        super().__init__(f"{code}: {message}" if message else code)


class WorkflowConflict(WorkflowError):
    def __init__(self, code: str = "scenario_revision_conflict"):
        super().__init__(code, status_code=409)


class WorkflowNotFound(WorkflowError):
    def __init__(self, code: str = "scenario_not_found"):
        super().__init__(code, status_code=404)
