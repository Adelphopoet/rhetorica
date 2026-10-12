"""Lazy public API keeps deterministic Temporal workflows free of I/O imports."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rhetorica_backend.domain.coding.admission import CodingAdmissionPolicy
    from rhetorica_backend.domain.coding.policies import (
        AdminAgentPolicyService,
        AgentPolicyValidationError,
        AgentPublicationPolicyEvaluator,
    )
    from rhetorica_backend.domain.coding.publication import PublicationPreparer, PublicationWorkItem
    from rhetorica_backend.domain.coding.publication_content_policy import PublicationDenied

__all__ = [
    "AdminAgentPolicyService",
    "AgentPolicyValidationError",
    "AgentPublicationPolicyEvaluator",
    "CodingAdmissionPolicy",
    "PublicationDenied",
    "PublicationPreparer",
    "PublicationWorkItem",
]


def __getattr__(name: str) -> Any:
    from importlib import import_module

    module = {
        "CodingAdmissionPolicy": "admission",
        "AdminAgentPolicyService": "policies",
        "AgentPolicyValidationError": "policies",
        "AgentPublicationPolicyEvaluator": "policies",
        "PublicationPreparer": "publication",
        "PublicationWorkItem": "publication",
        "PublicationDenied": "publication_content_policy",
    }.get(name)
    if module is None:
        raise AttributeError(name)
    return getattr(import_module(f"rhetorica_backend.domain.coding.{module}"), name)
