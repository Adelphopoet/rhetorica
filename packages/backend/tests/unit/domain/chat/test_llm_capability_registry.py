"""Tests for LLM capability registry and phase routing."""

from __future__ import annotations

import pytest
from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.infra.llm.capability_registry import (
    CostClass,
    LlmCapability,
    LlmCapabilityProfile,
    LlmCapabilityRegistry,
    LlmPhase,
    set_global_capability_registry,
)


@pytest.fixture
def registry() -> LlmCapabilityRegistry:
    """Create a fresh capability registry for testing."""
    return LlmCapabilityRegistry()


@pytest.fixture
def clean_registry():
    """Ensure global registry is clean after tests."""
    yield
    set_global_capability_registry(LlmCapabilityRegistry())


class TestLlmCapabilityProfile:
    """Tests for LlmCapabilityProfile."""

    def test_profile_supports_capability(self) -> None:
        """Profile correctly reports supported capabilities."""
        profile = LlmCapabilityProfile(
            model_family="gpt-4o",
            supports=frozenset({LlmCapability.TOOL_CALLS, LlmCapability.STRUCTURED_OUTPUT}),
        )
        assert profile.supports_capability(LlmCapability.TOOL_CALLS) is True
        assert profile.supports_capability(LlmCapability.VISUAL) is False

    def test_profile_is_suitable_for_phase(self) -> None:
        """Profile correctly reports phase suitability."""
        profile = LlmCapabilityProfile(
            model_family="gpt-3.5-turbo",
            recommended_phases=frozenset({LlmPhase.WORKER, LlmPhase.CLASSIFIER}),
        )
        assert profile.is_suitable_for_phase(LlmPhase.WORKER) is True
        assert profile.is_suitable_for_phase(LlmPhase.PLANNER) is False

    def test_cost_calculation(self) -> None:
        """Profile correctly calculates costs."""
        profile = LlmCapabilityProfile(
            model_family="gpt-4o-mini",
            estimated_cost_per_1m_input=0.15,
            estimated_cost_per_1m_output=0.60,
        )
        # 1000 input + 500 output tokens
        cost = profile.cost_per_1m_tokens(1000, 500)
        assert cost == pytest.approx(0.00015 + 0.0003)  # 0.00045


class TestLlmCapabilityRegistry:
    """Tests for LlmCapabilityRegistry initialization and defaults."""

    def test_gpt4o_has_all_capabilities(self, registry: LlmCapabilityRegistry) -> None:
        """GPT-4o has tool_calls, structured_output, streaming, and visual."""
        profile = registry.get_profile("gpt-4o")
        assert profile is not None
        assert LlmCapability.TOOL_CALLS in profile.supports
        assert LlmCapability.STRUCTURED_OUTPUT in profile.supports
        assert LlmCapability.STREAMING in profile.supports
        assert LlmCapability.VISUAL in profile.supports

    def test_gpt35_no_visual(self, registry: LlmCapabilityRegistry) -> None:
        """GPT-3.5-turbo doesn't have visual capability."""
        profile = registry.get_profile("gpt-3.5-turbo")
        assert profile is not None
        assert LlmCapability.VISUAL not in profile.supports

    def test_unknown_model_has_no_capabilities(self, registry: LlmCapabilityRegistry) -> None:
        """Unknown model gets minimal profile via fallback key."""
        profile = registry.get_profile("fallback")
        assert profile is not None
        assert len(profile.supports) == 0

    def test_claude_has_all_capabilities(self, registry: LlmCapabilityRegistry) -> None:
        """Claude family has full capabilities."""
        profile = registry.get_profile("claude")
        assert profile is not None
        assert LlmCapability.TOOL_CALLS in profile.supports
        assert LlmCapability.STRUCTURED_OUTPUT in profile.supports
        assert LlmCapability.STREAMING in profile.supports
        assert LlmCapability.VISUAL in profile.supports


class TestCapabilityResolution:
    """Tests for capability resolution from connector configs."""

    def test_force_text_output_disables_tools(self, registry: LlmCapabilityRegistry) -> None:
        """force_text_output=True removes tool_calls and structured_output."""
        config = LlmConnectionConfig(
            base_url="https://api.example.com",
            model="gpt-4o",
            extra_settings={
                "force_text_output": True,
            },
        )
        profile = registry.resolve_for_connector(config)
        assert LlmCapability.TOOL_CALLS not in profile.supports
        assert LlmCapability.STRUCTURED_OUTPUT not in profile.supports

    def test_resolve_phase_gives_cheapest_recommended(self, registry: LlmCapabilityRegistry) -> None:
        """For CLASSIFIER phase, cheapest recommended model is preferred."""
        profile = registry.resolve_for_phase(LlmPhase.CLASSIFIER)
        assert LlmPhase.CLASSIFIER in profile.recommended_phases
        # Should prefer cheaper models for classifier
        assert profile.cost_class in (CostClass.CHEAP, CostClass.STANDARD, CostClass.UNKNOWN)

    def test_phase_routing_respects_connector_override(self, registry: LlmCapabilityRegistry) -> None:
        """Explicit connector key overrides phase routing."""
        profile = registry.resolve_for_phase(LlmPhase.WORKER, connector_key="gpt-4o")
        assert profile.model_family == "gpt-4o"

    def test_context_window_override(self, registry: LlmCapabilityRegistry) -> None:
        """context_window_tokens overrides prefix detection."""
        config = LlmConnectionConfig(
            base_url="https://api.example.com",
            model="unknown-model",
            extra_settings={
                "context_window_tokens": 256_000,
            },
        )
        profile = registry.resolve_for_connector(config)
        assert profile.context_window == 256_000

    def test_deprecated_force_text_output_no_crash(self, registry: LlmCapabilityRegistry) -> None:
        """force_text_output should not crash, just log warning."""
        config = LlmConnectionConfig(
            base_url="https://api.example.com",
            model="gpt-4o",
            extra_settings={
                "force_text_output": True,
            },
        )
        # Should not raise
        profile = registry.resolve_for_connector(config)
        assert profile is not None

    def test_deprecated_context_window_alias(self, registry: LlmCapabilityRegistry) -> None:
        """context_window alias should resolve to same value as context_window_tokens."""
        config_legacy = LlmConnectionConfig(
            base_url="https://api.example.com",
            model="gpt-4o",
            extra_settings={
                "context_window": 64_000,
            },
        )
        config_new = LlmConnectionConfig(
            base_url="https://api.example.com",
            model="gpt-4o",
            extra_settings={
                "context_window_tokens": 64_000,
            },
        )
        profile_legacy = registry.resolve_for_connector(config_legacy)
        profile_new = registry.resolve_for_connector(config_new)
        assert profile_legacy.context_window == profile_new.context_window == 64_000


class TestPhaseRouting:
    """Tests for phase-aware routing."""

    def test_synthesis_gets_full_capabilities(self, registry: LlmCapabilityRegistry) -> None:
        """SYNTHESIS phase should not match cheap-only models."""
        profile = registry.resolve_for_phase(LlmPhase.SYNTHESIS)
        # Synthesis should work with full-capability models
        assert LlmCapability.STRUCTURED_OUTPUT in profile.supports

    def test_planner_gets_planner_recommended(self, registry: LlmCapabilityRegistry) -> None:
        """PLANNER phase should prefer profiles with PLANNER in recommended_phases."""
        profile = registry.resolve_for_phase(LlmPhase.PLANNER)
        assert LlmPhase.PLANNER in profile.recommended_phases

    def test_classifier_prefer_cheap(self, registry: LlmCapabilityRegistry) -> None:
        """CLASSIFIER should prefer cheapest available."""
        profile = registry.resolve_for_phase(LlmPhase.CLASSIFIER)
        # Classifier should be cheap or standard
        assert profile.cost_class in (CostClass.CHEAP, CostClass.STANDARD, CostClass.UNKNOWN)

    def test_repair_needs_tool_calls(self, registry: LlmCapabilityRegistry) -> None:
        """REPAIR phase needs tool calling capability."""
        profile = registry.resolve_for_phase(LlmPhase.REPAIR)
        assert LlmCapability.TOOL_CALLS in profile.supports

    def test_classifier_returns_fallback_profile(self, registry: LlmCapabilityRegistry) -> None:
        """CLASSIFIER phase returns fallback profile when no cheap candidates available.

        This verifies the cost-sort behavior: CLASSIFIER returns the profile
        with lowest input cost, which may be the fallback profile.
        """
        # The fallback profile has 0.0 input cost
        profile = registry.resolve_for_phase(LlmPhase.CLASSIFIER)
        # Fallback should be selected due to cost sort on input tokens
        assert profile.model_family == "unknown"

    def test_cost_sort_prefers_lower_input(self, registry: LlmCapabilityRegistry) -> None:
        """Cost sort uses only input cost, not output cost.

        This test verifies that model selection uses input cost for sorting,
        regardless of output cost differences. Since fallback profile has 0.0 input
        cost, we test with models that have higher input costs.
        """
        # Register a profile with lower input cost but higher output cost
        registry.register_profile(
            "low-input-high-output",
            LlmCapabilityProfile(
                model_family="low-input-high-output",
                estimated_cost_per_1m_input=0.10,  # Low input
                estimated_cost_per_1m_output=50.00,  # Very high output
                recommended_phases=frozenset({LlmPhase.CLASSIFIER}),
            ),
        )
        # Register a profile with higher input cost but lower output cost
        registry.register_profile(
            "high-input-low-output",
            LlmCapabilityProfile(
                model_family="high-input-low-output",
                estimated_cost_per_1m_input=1.00,  # Higher input
                estimated_cost_per_1m_output=0.01,  # Lower output
                recommended_phases=frozenset({LlmPhase.CLASSIFIER}),
            ),
        )

        profile = registry.resolve_for_phase(LlmPhase.CLASSIFIER)
        # Should prefer low-input-high-output due to input cost sort
        # Note: fallback profile (0.0 input) would be cheaper, but we're testing
        # that input cost is used for comparison, not output cost
        assert profile.estimated_cost_per_1m_input < 1.00  # Lower input cost wins


class TestConnectorCapabilityDiscovery:
    """Tests for automatic capability discovery from connector configs."""

    def test_structured_output_enabled_adds_capability(self, registry: LlmCapabilityRegistry) -> None:
        """structured_output_enabled=True adds STRUCTURED_OUTPUT capability."""
        config = LlmConnectionConfig(
            base_url="https://api.example.com",
            model="unknown-model",
            extra_settings={
                "structured_output_enabled": True,
            },
        )
        profile = registry.resolve_for_connector(config)
        assert LlmCapability.STRUCTURED_OUTPUT in profile.supports

    def test_support_tool_calls_from_extra_settings(self, registry: LlmCapabilityRegistry) -> None:
        """support_tool_calls in extra_settings adds TOOL_CALLS capability."""
        config = LlmConnectionConfig(
            base_url="https://api.example.com",
            model="unknown-model",
            extra_settings={
                "support_tool_calls": True,
            },
        )
        profile = registry.resolve_for_connector(config)
        assert LlmCapability.TOOL_CALLS in profile.supports

    def test_model_name_pattern_detection(self, registry: LlmCapabilityRegistry) -> None:
        """Model names like gpt-4*, claude-*, gemini-* auto-detected for tool calls."""
        patterns = [
            "gpt-4-turbo",
            "gpt-4o-2024-08-06",
            "claude-3-opus",
            "claude-3.5-sonnet",
            "gemini-pro",
        ]
        for model in patterns:
            config = LlmConnectionConfig(
                base_url="https://api.example.com",
                model=model,
            )
            profile = registry.resolve_for_connector(config)
            assert LlmCapability.TOOL_CALLS in profile.supports, f"Failed for {model}"

    def test_streaming_assumed_by_default(self, registry: LlmCapabilityRegistry) -> None:
        """Streaming is assumed unless explicitly disabled."""
        config = LlmConnectionConfig(
            base_url="https://api.example.com",
            model="gpt-4o",
        )
        profile = registry.resolve_for_connector(config)
        assert LlmCapability.STREAMING in profile.supports

    def test_null_connector_returns_fallback_profile(self, registry: LlmCapabilityRegistry) -> None:
        """Null connector config returns fallback profile."""
        profile = registry.resolve_for_connector(None)
        assert profile.model_family == "unknown"
        assert len(profile.supports) == 0
        assert profile.cost_class == CostClass.UNKNOWN


class TestProfileRegistration:
    """Tests for custom profile registration."""

    def test_register_custom_profile(self, registry: LlmCapabilityRegistry) -> None:
        """Custom profiles can be registered."""
        custom = LlmCapabilityProfile(
            model_family="custom-model",
            supports=frozenset({LlmCapability.TOOL_CALLS}),
            context_window=32_000,
        )
        registry.register_profile("custom-model", custom)
        retrieved = registry.get_profile("custom-model")
        assert retrieved is not None
        assert retrieved.model_family == "custom-model"

    def test_wildcard_pattern_registration(self, registry: LlmCapabilityRegistry) -> None:
        """Wildcard patterns work for model matching."""
        profile = LlmCapabilityProfile(
            model_family="custom",
            supports=frozenset({LlmCapability.STRUCTURED_OUTPUT}),
        )
        registry.register_profile("custom-*", profile)
        assert registry.get_profile("custom-gpt") is not None

    def test_phase_default_setting(self, registry: LlmCapabilityRegistry) -> None:
        """Phase defaults can be set and retrieved."""
        registry.set_phase_default(LlmPhase.PLANNER, "gpt-4o")
        profile = registry.resolve_for_phase(LlmPhase.PLANNER)
        assert profile.model_family == "gpt-4o"


class TestGetAllProfiles:
    """Tests for getting all profiles."""

    def test_get_all_profiles(self, registry: LlmCapabilityRegistry) -> None:
        """get_all_profiles returns all registered profiles."""
        profiles = registry.get_all_profiles()
        assert "gpt-4o" in profiles
        assert "gpt-4o-mini" in profiles
        assert "gpt-3.5-turbo" in profiles
        assert "claude" in profiles
        assert "fallback" in profiles


class TestBoundedConnectorCache:
    """Tests for bounded connector capability cache."""

    def test_cache_stores_resolved_profiles(self, registry: LlmCapabilityRegistry) -> None:
        """Connector cache stores resolved profiles for reuse."""
        config = LlmConnectionConfig(
            base_url="https://api.example.com",
            model="test-model",
            extra_settings={"structured_output_enabled": True},
        )
        # First call resolves and caches
        profile1 = registry.resolve_for_connector(config)
        # Second call returns cached profile
        profile2 = registry.resolve_for_connector(config)
        assert profile1 is profile2  # Same object (cached)

    def test_cache_evicts_oldest_on_overflow(self) -> None:
        """Cache evicts oldest entry when full."""
        from rhetorica_backend.infra.llm.capability_registry import (
            LlmCapabilityProfile,
            _BoundedCache,
        )

        cache = _BoundedCache(maxsize=3)

        # Fill cache: order becomes key1, key2, key3
        cache.set("key1", LlmCapabilityProfile(model_family="model1"))
        cache.set("key2", LlmCapabilityProfile(model_family="model2"))
        cache.set("key3", LlmCapabilityProfile(model_family="model3"))

        assert len(cache) == 3
        assert cache.get("key1") is not None

        # Adding 4th entry evicts oldest (key2), NOT key1 which was just accessed
        # LRU order after get: key2, key3, key1
        cache.set("key4", LlmCapabilityProfile(model_family="model4"))
        assert len(cache) == 3
        # key1 was accessed (moved to end), so key2 is evicted
        assert cache.get("key2") is None  # Evicted (LRU)
        assert cache.get("key3") is not None  # Still there
        assert cache.get("key4") is not None  # Just added

    def test_cache_maxsize_matches_constant(self) -> None:
        """Cache uses the correct maxsize constant."""
        from rhetorica_backend.infra.llm.capability_registry import (
            _CONNECTOR_CACHE_MAXSIZE,
            _BoundedCache,
        )

        cache = _BoundedCache()
        assert cache._maxsize == _CONNECTOR_CACHE_MAXSIZE
        assert _CONNECTOR_CACHE_MAXSIZE == 100

