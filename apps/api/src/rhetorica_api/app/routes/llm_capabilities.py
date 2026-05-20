"""LLM capability registry API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from rhetorica_api.app.auth import require_admin_access

router = APIRouter(
    prefix="/v1/admin/llm-capabilities",
    tags=["llm-capabilities"],
    dependencies=[Depends(require_admin_access)],
)


class CapabilityProfileResponse(BaseModel):
    """Response model for a single capability profile."""

    model_family: str
    version: str
    supports: list[str]
    context_window: int
    max_output_tokens: int
    cost_class: str
    recommended_phases: list[str]
    known_quirks: list[str]
    estimated_cost_per_1m_input: float
    estimated_cost_per_1m_output: float


class PhaseCapabilityResponse(BaseModel):
    """Response model for phase-specific capability listing."""

    phase: str
    recommended_model: str
    supports: list[str]
    cost_class: str


class DiscoverCapabilityRequest(BaseModel):
    """Request to auto-discover capabilities for a connector."""

    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    extra_settings: dict[str, Any] = {}


class DiscoverCapabilityResponse(BaseModel):
    """Response with auto-discovered capabilities."""

    detected_profile: CapabilityProfileResponse
    warnings: list[str]


def _profile_to_response(
    profile: Any,
) -> CapabilityProfileResponse:
    """Convert internal profile to API response."""
    return CapabilityProfileResponse(
        model_family=profile.model_family,
        version=profile.version,
        supports=[c.value for c in profile.supports],
        context_window=profile.context_window,
        max_output_tokens=profile.max_output_tokens,
        cost_class=profile.cost_class.value if hasattr(profile.cost_class, "value") else str(profile.cost_class),
        recommended_phases=[p.value for p in profile.recommended_phases],
        known_quirks=list(profile.known_quirks),
        estimated_cost_per_1m_input=profile.estimated_cost_per_1m_input,
        estimated_cost_per_1m_output=profile.estimated_cost_per_1m_output,
    )


@router.get("", response_model=list[CapabilityProfileResponse])
def list_capabilities() -> list[CapabilityProfileResponse]:
    """
    List all registered LLM capability profiles.

    Returns a list of all known model profiles with their capabilities.
    """
    from rhetorica_backend.infra.llm.capability_registry import get_global_capability_registry

    registry = get_global_capability_registry()
    profiles = registry.get_all_profiles()
    return [_profile_to_response(p) for p in profiles.values()]


@router.get("/{profile_key}", response_model=CapabilityProfileResponse)
def get_capability_profile(profile_key: str) -> CapabilityProfileResponse:
    """
    Get detailed profile for a specific model.

    Args:
        profile_key: Model identifier (e.g., "gpt-4o", "claude", "unknown")

    Returns:
        Detailed capability profile for the model.
    """
    from rhetorica_backend.infra.llm.capability_registry import get_global_capability_registry

    registry = get_global_capability_registry()
    profile = registry.get_profile(profile_key)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile_not_found")
    return _profile_to_response(profile)


@router.post("/discover", response_model=DiscoverCapabilityResponse)
def discover_capabilities(
    request: DiscoverCapabilityRequest,
) -> DiscoverCapabilityResponse:
    """
    Auto-discover capabilities for a model configuration.

    Makes a test probe call to determine capabilities based on model name
    and extra_settings.

    Args:
        request: Model configuration to probe

    Returns:
        Detected capability profile with any warnings.
    """
    from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
    from rhetorica_backend.infra.llm.capability_registry import get_global_capability_registry

    registry = get_global_capability_registry()
    warnings: list[str] = []

    # Build connector config
    config = LlmConnectionConfig(
        base_url=request.base_url,
        model=request.model,
        extra_settings=request.extra_settings,
    )

    # Resolve profile
    profile = registry.resolve_for_connector(config)

    # Collect deprecation warnings
    if request.extra_settings.get("force_text_output") is not None:
        warnings.append(
            "force_text_output is deprecated. "
            "Use structured_output_enabled instead."
        )
    if request.extra_settings.get("context_window") is not None:
        warnings.append(
            "context_window is deprecated. "
            "Use context_window_tokens instead."
        )

    return DiscoverCapabilityResponse(
        detected_profile=_profile_to_response(profile),
        warnings=warnings,
    )


@router.get("/phases/overview", response_model=list[PhaseCapabilityResponse])
def list_phase_capabilities() -> list[PhaseCapabilityResponse]:
    """
    List recommended models for each orchestration phase.

    Returns a summary of which models are recommended for each phase.
    """
    from rhetorica_backend.infra.llm.capability_registry import (
        LlmPhase,
        get_global_capability_registry,
    )

    registry = get_global_capability_registry()
    results: list[PhaseCapabilityResponse] = []

    for phase in LlmPhase:
        profile = registry.resolve_for_phase(phase)
        results.append(
            PhaseCapabilityResponse(
                phase=phase.value,
                recommended_model=profile.model_family,
                supports=[c.value for c in profile.supports],
                cost_class=profile.cost_class.value if hasattr(profile.cost_class, "value") else str(profile.cost_class),
            )
        )

    return results
