"""LLM capability registry with phase-aware model routing."""

from __future__ import annotations

import fnmatch
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig

logger = logging.getLogger(__name__)

# Maximum size for connector capability cache
_CONNECTOR_CACHE_MAXSIZE = 100


class _BoundedCache:
    """LRU-style bounded cache with maxsize limit."""

    def __init__(self, maxsize: int = 100) -> None:
        self._cache: OrderedDict[str, LlmCapabilityProfile] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> LlmCapabilityProfile | None:
        """Get item, moving it to end (most recently used)."""
        if key not in self._cache:
            return None
        # Move to end (most recently used)
        self._cache.move_to_end(key)
        return self._cache[key]

    def set(self, key: str, value: LlmCapabilityProfile) -> None:
        """Set item, evicting oldest if at capacity."""
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = value
            return
        # Evict oldest if at capacity
        if len(self._cache) >= self._maxsize:
            self._cache.popitem(last=False)
        self._cache[key] = value

    def __len__(self) -> int:
        return len(self._cache)


class LlmCapability(Enum):
    """Capabilities that an LLM model may support."""

    TOOL_CALLS = "tool_calls"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    VISUAL = "visual"


class LlmPhase(Enum):
    """Phases in the chat orchestration pipeline that may use different models."""

    PLANNER = "planner"
    WORKER = "worker"
    SYNTHESIS = "synthesis"
    REPAIR = "repair"
    CLASSIFIER = "classifier"


# Cost tiers for model selection
class CostClass(str, Enum):
    PREMIUM = "premium"
    STANDARD = "standard"
    CHEAP = "cheap"
    UNKNOWN = "unknown"


# Default cost estimates per 1M tokens (input, output)
DEFAULT_COSTS: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-3.5-turbo": (0.50, 1.50),
    "claude": (3.00, 15.00),  # approximate for claude-3/4 family
}

# Context windows by model family
CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-3.5-turbo": 16_385,
    "claude": 200_000,
    "gemini": 1_000_000,
}


@dataclass(frozen=True)
class LlmCapabilityProfile:
    """Immutable capability profile for an LLM model."""

    model_family: str
    version: str = "v1"
    supports: frozenset[LlmCapability] = frozenset()
    context_window: int = 16_384
    max_output_tokens: int = 1_200
    cost_class: CostClass = CostClass.UNKNOWN
    recommended_phases: frozenset[LlmPhase] = frozenset()
    known_quirks: tuple[str, ...] = ()
    estimated_cost_per_1m_input: float = 0.0
    estimated_cost_per_1m_output: float = 0.0

    def supports_capability(self, capability: LlmCapability) -> bool:
        """Check if this profile supports the given capability."""
        return capability in self.supports

    def is_suitable_for_phase(self, phase: LlmPhase) -> bool:
        """Check if this model is suitable for the given phase."""
        return phase in self.recommended_phases

    def cost_per_1m_tokens(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate estimated cost for given token counts."""
        return (
            (input_tokens / 1_000_000) * self.estimated_cost_per_1m_input
            + (output_tokens / 1_000_000) * self.estimated_cost_per_1m_output
        )


@dataclass
class LlmCapabilityRegistry:
    """
    Central registry of LLM model capability profiles with phase-aware routing.

    This registry maintains known capability profiles for LLM models and
    provides phase-aware model selection for the orchestration pipeline.
    """

    profiles: dict[str, LlmCapabilityProfile] = field(default_factory=dict)
    default_profile_key: str = "fallback"
    phase_models: dict[LlmPhase, str] = field(default_factory=dict)
    connector_caps: _BoundedCache = field(default_factory=lambda: _BoundedCache(_CONNECTOR_CACHE_MAXSIZE))

    def __post_init__(self) -> None:
        """Initialize with default profiles if empty."""
        if not self.profiles:
            self._register_default_profiles()

    def _register_default_profiles(self) -> None:
        """Register built-in profiles for common models."""
        # GPT-4o - premium, full capabilities
        self.register_profile(
            "gpt-4o",
            LlmCapabilityProfile(
                model_family="gpt-4o",
                supports=frozenset({
                    LlmCapability.TOOL_CALLS,
                    LlmCapability.STRUCTURED_OUTPUT,
                    LlmCapability.STREAMING,
                    LlmCapability.VISUAL,
                }),
                context_window=128_000,
                cost_class=CostClass.PREMIUM,
                recommended_phases=frozenset(LlmPhase),
                known_quirks=(),
                estimated_cost_per_1m_input=2.50,
                estimated_cost_per_1m_output=10.00,
            ),
        )

        # GPT-4o-mini - standard, good for most phases
        self.register_profile(
            "gpt-4o-mini",
            LlmCapabilityProfile(
                model_family="gpt-4o-mini",
                supports=frozenset({
                    LlmCapability.TOOL_CALLS,
                    LlmCapability.STRUCTURED_OUTPUT,
                    LlmCapability.STREAMING,
                }),
                context_window=128_000,
                cost_class=CostClass.CHEAP,
                recommended_phases=frozenset({
                    LlmPhase.WORKER,
                    LlmPhase.SYNTHESIS,
                    LlmPhase.REPAIR,
                    LlmPhase.CLASSIFIER,
                }),
                known_quirks=(),
                estimated_cost_per_1m_input=0.15,
                estimated_cost_per_1m_output=0.60,
            ),
        )

        # GPT-3.5-turbo - cheap, limited phases
        self.register_profile(
            "gpt-3.5-turbo",
            LlmCapabilityProfile(
                model_family="gpt-3.5-turbo",
                supports=frozenset({
                    LlmCapability.TOOL_CALLS,
                    LlmCapability.STRUCTURED_OUTPUT,
                    LlmCapability.STREAMING,
                }),
                context_window=16_385,
                cost_class=CostClass.CHEAP,
                recommended_phases=frozenset({LlmPhase.WORKER, LlmPhase.CLASSIFIER}),
                known_quirks=("limited_context_window",),
                estimated_cost_per_1m_input=0.50,
                estimated_cost_per_1m_output=1.50,
            ),
        )

        # Claude family - premium, full capabilities
        self.register_profile(
            "claude",
            LlmCapabilityProfile(
                model_family="claude",
                supports=frozenset({
                    LlmCapability.TOOL_CALLS,
                    LlmCapability.STRUCTURED_OUTPUT,
                    LlmCapability.STREAMING,
                    LlmCapability.VISUAL,
                }),
                context_window=200_000,
                cost_class=CostClass.PREMIUM,
                recommended_phases=frozenset(LlmPhase),
                known_quirks=(),
                estimated_cost_per_1m_input=3.00,
                estimated_cost_per_1m_output=15.00,
            ),
        )

        # Fallback profile for unknown models
        self.register_profile(
            "fallback",
            LlmCapabilityProfile(
                model_family="unknown",
                supports=frozenset(),
                context_window=16384,
                max_output_tokens=1200,
                cost_class=CostClass.UNKNOWN,
                recommended_phases=frozenset({LlmPhase.CLASSIFIER}),
                known_quirks=("capability_unknown",),
                estimated_cost_per_1m_input=0.0,
                estimated_cost_per_1m_output=0.0,
            ),
        )

    def register_profile(self, key: str, profile: LlmCapabilityProfile) -> None:
        """
        Register a capability profile for a model family.

        Args:
            key: Model identifier (e.g., "gpt-4o", "claude", or pattern like "claude-*")
            profile: Capability profile for the model
        """
        self.profiles[key] = profile
        # Also index by model family
        if profile.model_family and profile.model_family not in self.profiles:
            self.profiles[profile.model_family] = profile

    def resolve_for_phase(
        self,
        phase: LlmPhase,
        connector_key: str | None = None,
    ) -> LlmCapabilityProfile:
        """
        Get the capability profile best suited for this phase.

        Args:
            phase: The orchestration phase
            connector_key: Optional connector/model identifier for explicit selection

        Returns:
            Best matching LlmCapabilityProfile for the phase
        """
        # If explicit connector key provided, try to match it
        if connector_key:
            profile = self._find_profile_by_key(connector_key)
            if profile:
                return profile

        # Use phase-specific default if configured
        if phase in self.phase_models:
            key = self.phase_models[phase]
            profile = self._find_profile_by_key(key)
            if profile:
                return profile

        # Find cheapest suitable model for the phase
        return self._find_cheapest_for_phase(phase)

    def resolve_for_connector(
        self,
        connector_config: LlmConnectionConfig | None,
    ) -> LlmCapabilityProfile:
        """
        Resolve capabilities from a connector configuration.

        Probes extra_settings for capability hints and matches against
        known profiles or falls back to detection.

        Args:
            connector_config: LLM connector configuration

        Returns:
            Resolved LlmCapabilityProfile
        """
        if connector_config is None:
            return self.profiles[self.default_profile_key]

        model = connector_config.model.lower()
        extra = connector_config.extra_settings

        # Check if we have a pre-computed profile for this connector
        cache_key = f"{connector_config.base_url}:{model}"
        cached = self.connector_caps.get(cache_key)
        if cached is not None:
            return cached

        # Handle legacy force_text_output (deprecated)
        force_text_output = extra.get("force_text_output")
        if isinstance(force_text_output, bool):
            logger.warning(
                "llm_capability.deprecated_force_text_output",
                extra={
                    "model": model,
                    "force_text_output": force_text_output,
                },
            )

        # Handle legacy context_window alias (deprecated)
        context_window_legacy = extra.get("context_window")
        if context_window_legacy is not None and extra.get("context_window_tokens") is None:
            logger.warning(
                "llm_capability.deprecated_context_window_alias",
                extra={
                    "model": model,
                    "context_window": context_window_legacy,
                },
            )

        # Build profile from connector config
        profile = self._build_profile_from_config(
            model=model,
            extra=extra,
            force_text_output=force_text_output,
        )

        # Cache the resolved profile (bounded cache, evicts oldest when full)
        self.connector_caps.set(cache_key, profile)
        return profile

    def _build_profile_from_config(
        self,
        model: str,
        extra: dict[str, Any],
        force_text_output: Any = None,
    ) -> LlmCapabilityProfile:
        """Build a capability profile from connector configuration."""
        supports: set[LlmCapability] = set()

        # Structured output from extra_settings
        structured_output_enabled = extra.get("structured_output_enabled")
        if isinstance(structured_output_enabled, bool) and structured_output_enabled:
            supports.add(LlmCapability.STRUCTURED_OUTPUT)

        # Tool calls detection
        support_tool_calls = extra.get("support_tool_calls")
        tool_capable_by_name = any(
            fnmatch.fnmatch(model, pattern)
            for pattern in ["gpt-4*", "claude-*", "gemini-*"]
        )

        if isinstance(support_tool_calls, bool):
            if support_tool_calls:
                supports.add(LlmCapability.TOOL_CALLS)
        elif tool_capable_by_name:
            supports.add(LlmCapability.TOOL_CALLS)

        # Streaming - always assumed unless explicitly false
        streaming_disabled = extra.get("streaming_enabled") is False
        if not streaming_disabled:
            supports.add(LlmCapability.STREAMING)

        # Visual - detected from model name
        if any(fnmatch.fnmatch(model, pattern) for pattern in ["gpt-4o*", "claude-*", "gemini-*"]):
            supports.add(LlmCapability.VISUAL)

        # Context window detection
        context_window = self._resolve_context_window(model, extra)

        # Cost class detection
        cost_class = self._resolve_cost_class(model)

        # Phase recommendations based on cost class
        if cost_class == CostClass.PREMIUM:
            recommended_phases = frozenset(LlmPhase)
        elif cost_class == CostClass.STANDARD:
            recommended_phases = frozenset({
                LlmPhase.WORKER,
                LlmPhase.SYNTHESIS,
                LlmPhase.REPAIR,
                LlmPhase.CLASSIFIER,
            })
        elif cost_class == CostClass.CHEAP:
            recommended_phases = frozenset({LlmPhase.WORKER, LlmPhase.CLASSIFIER})
        else:
            recommended_phases = frozenset({LlmPhase.CLASSIFIER})

        # Apply force_text_output deprecation
        if isinstance(force_text_output, bool) and force_text_output:
            # Remove TOOL_CALLS and STRUCTURED_OUTPUT when text-only mode
            supports.discard(LlmCapability.TOOL_CALLS)
            supports.discard(LlmCapability.STRUCTURED_OUTPUT)

        # Get cost estimates
        cost_input, cost_output = self._resolve_cost_estimates(model)

        return LlmCapabilityProfile(
            model_family=self._extract_model_family(model),
            supports=frozenset(supports),
            context_window=context_window,
            cost_class=cost_class,
            recommended_phases=recommended_phases,
            estimated_cost_per_1m_input=cost_input,
            estimated_cost_per_1m_output=cost_output,
        )

    def _resolve_context_window(self, model: str, extra: dict[str, Any]) -> int:
        """Resolve context window from extra_settings or model name."""
        # Prefer context_window_tokens, then fallback to context_window alias
        raw = extra.get("context_window_tokens")
        if raw is None:
            raw = extra.get("context_window")

        if isinstance(raw, int | float):
            value = int(raw)
            if value > 0:
                return value

        # Fall back to model name detection
        for family, window in CONTEXT_WINDOWS.items():
            if fnmatch.fnmatch(model, f"{family}*"):
                return window

        return 16_384  # Default fallback

    def _resolve_cost_class(self, model: str) -> CostClass:
        """Resolve cost class from model name."""
        model_lower = model.lower()
        if "gpt-4o" in model_lower and "mini" not in model_lower:
            return CostClass.PREMIUM
        if "gpt-4o-mini" in model_lower or "gpt-3.5" in model_lower:
            return CostClass.CHEAP
        if "claude" in model_lower:
            return CostClass.PREMIUM
        if "gemini" in model_lower:
            return CostClass.PREMIUM
        return CostClass.UNKNOWN

    def _resolve_cost_estimates(self, model: str) -> tuple[float, float]:
        """Resolve cost estimates from model name."""
        model_lower = model.lower()
        for family, costs in DEFAULT_COSTS.items():
            if family in model_lower:
                return costs
        return (0.0, 0.0)

    def _extract_model_family(self, model: str) -> str:
        """Extract model family identifier from model name."""
        model_lower = model.lower()
        for family in ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo", "claude", "gemini"]:
            if family in model_lower:
                return family
        return "unknown"

    def _find_profile_by_key(self, key: str) -> LlmCapabilityProfile | None:
        """Find a profile by exact key match or wildcard pattern."""
        # Try exact match first
        if key in self.profiles:
            return self.profiles[key]

        # Try wildcard patterns
        for pattern, profile in self.profiles.items():
            if "*" in pattern and fnmatch.fnmatch(key.lower(), pattern.lower()):
                return profile

        return None

    def _find_cheapest_for_phase(self, phase: LlmPhase) -> LlmCapabilityProfile:
        """
        Find the cheapest suitable model for a given phase.

        Note: Sorting uses input cost only. This is intentional for phase selection:
        - Input tokens dominate cost for short-phase queries (e.g., CLASSIFIER)
        - Output cost is less predictable at selection time
        - Simpler, more predictable selection behavior
        """
        candidates: list[tuple[float, LlmCapabilityProfile]] = []

        for profile in self.profiles.values():
            if phase in profile.recommended_phases:
                candidates.append((
                    profile.estimated_cost_per_1m_input,
                    profile,
                ))

        if candidates:
            # Sort by input cost and return cheapest
            candidates.sort(key=lambda x: x[0])
            return candidates[0][1]

        # Fall back to fallback profile
        return self.profiles[self.default_profile_key]

    def get_all_profiles(self) -> dict[str, LlmCapabilityProfile]:
        """Return all registered profiles."""
        return dict(self.profiles)

    def get_profile(self, key: str) -> LlmCapabilityProfile | None:
        """Get a specific profile by key."""
        return self._find_profile_by_key(key)

    def set_phase_default(self, phase: LlmPhase, model_key: str) -> None:
        """Set default model for a specific phase."""
        self.phase_models[phase] = model_key


# Global singleton registry instance
_global_registry: LlmCapabilityRegistry | None = None


def get_global_capability_registry() -> LlmCapabilityRegistry:
    """Get the global capability registry singleton."""
    global _global_registry
    if _global_registry is None:
        _global_registry = LlmCapabilityRegistry()
    return _global_registry


def set_global_capability_registry(registry: LlmCapabilityRegistry) -> None:
    """Set the global capability registry (for testing)."""
    global _global_registry
    _global_registry = registry
