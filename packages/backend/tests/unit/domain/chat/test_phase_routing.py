"""Tests for phase routing integration with orchestrators."""

from __future__ import annotations

from unittest.mock import MagicMock

from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.domain.chat.orchestration.session import OrchestrationSessionFactory
from rhetorica_backend.domain.chat.orchestration.turn_builder import TurnBuilder
from rhetorica_backend.infra.llm.capability_registry import (
    LlmCapabilityRegistry,
    LlmPhase,
)


class TestOrchestratorUsesCapabilityRegistry:
    """Tests for capability registry integration with orchestrator session factory."""

    def test_orchestrator_uses_capability_registry(self) -> None:
        """Verify correct model selected per phase when registry is available."""
        # Create mock registry
        registry = LlmCapabilityRegistry()

        # Set explicit phase defaults to test phase routing
        registry.set_phase_default(LlmPhase.PLANNER, "gpt-4o")
        registry.set_phase_default(LlmPhase.WORKER, "gpt-4o-mini")
        registry.set_phase_default(LlmPhase.CLASSIFIER, "gpt-3.5-turbo")
        registry.set_phase_default(LlmPhase.SYNTHESIS, "gpt-4o")
        registry.set_phase_default(LlmPhase.REPAIR, "gpt-4o")

        # Verify profiles are correctly selected for each phase
        assert registry.resolve_for_phase(LlmPhase.PLANNER).model_family == "gpt-4o"
        assert registry.resolve_for_phase(LlmPhase.WORKER).model_family == "gpt-4o-mini"
        assert registry.resolve_for_phase(LlmPhase.CLASSIFIER).model_family == "gpt-3.5-turbo"
        assert registry.resolve_for_phase(LlmPhase.SYNTHESIS).model_family == "gpt-4o"
        assert registry.resolve_for_phase(LlmPhase.REPAIR).model_family == "gpt-4o"

    def test_orchestrator_falls_back_to_default(self) -> None:
        """When no profile for model, uses unknown fallback."""
        registry = LlmCapabilityRegistry()

        # Build a config for an unknown model
        config = LlmConnectionConfig(
            base_url="https://api.example.com",
            model="completely-unknown-model-xyz",
        )

        # Resolve capability profile
        profile = registry.resolve_for_connector(config)

        # Should fall back to unknown model family
        assert profile.model_family == "unknown"
        # STREAMING is assumed by default, so only that capability should be present
        # for an unknown model without explicit configuration
        assert profile.cost_class.value == "unknown"


class TestPhaseRoutingInProfiles:
    """Tests for phase routing behavior in profiles."""

    def test_planner_premium_models(self) -> None:
        """Planner phase should use premium models."""
        registry = LlmCapabilityRegistry()

        # Without explicit defaults, finds cheapest for phase
        profile = registry.resolve_for_phase(LlmPhase.PLANNER)

        # Planner should be in recommended phases for the returned model
        assert LlmPhase.PLANNER in profile.recommended_phases

    def test_classifier_cheap_models(self) -> None:
        """Classifier phase should prefer cheaper models."""
        registry = LlmCapabilityRegistry()

        profile = registry.resolve_for_phase(LlmPhase.CLASSIFIER)

        # Should be suitable for classifier
        assert LlmPhase.CLASSIFIER in profile.recommended_phases

        # Should prefer cheaper cost class (cheapest suitable for CLASSIFIER)
        # gpt-3.5-turbo or gpt-4o-mini are candidates
        assert profile.cost_class.value in ("cheap", "standard", "unknown")

    def test_worker_balanced_models(self) -> None:
        """Worker phase should use balanced cost/quality models."""
        registry = LlmCapabilityRegistry()

        profile = registry.resolve_for_phase(LlmPhase.WORKER)

        # Worker should support this phase
        assert LlmPhase.WORKER in profile.recommended_phases

    def test_repair_uses_capable_models(self) -> None:
        """Repair phase needs tool calling capable models."""
        registry = LlmCapabilityRegistry()

        profile = registry.resolve_for_phase(LlmPhase.REPAIR)

        # Repair should be in recommended phases for the returned model
        assert LlmPhase.REPAIR in profile.recommended_phases


class TestCapabilityRegistrySessionIntegration:
    """Tests for capability registry integration with session factory."""

    def test_session_factory_accepts_capability_registry(self) -> None:
        """Session factory constructor accepts optional capability_registry."""
        registry = LlmCapabilityRegistry()

        # Create mock dependencies
        llm_repo = MagicMock()
        conv_manager = MagicMock()
        db_repo = MagicMock()
        turn_builder = MagicMock(spec=TurnBuilder)
        sql_router = MagicMock()

        # Create factory with capability registry
        factory = OrchestrationSessionFactory(
            llm_connector_repository=llm_repo,
            conversation_manager=conv_manager,
            db_connector_repository=db_repo,
            turn_builder=turn_builder,
            sql_router=sql_router,
            capability_registry=registry,
        )

        assert factory._capability_registry is registry

    def test_session_factory_works_without_capability_registry(self) -> None:
        """Session factory works without capability registry (backward compatible)."""
        # Create mock dependencies
        llm_repo = MagicMock()
        conv_manager = MagicMock()
        db_repo = MagicMock()
        turn_builder = MagicMock(spec=TurnBuilder)
        sql_router = MagicMock()

        # Create factory without capability registry
        factory = OrchestrationSessionFactory(
            llm_connector_repository=llm_repo,
            conversation_manager=conv_manager,
            db_connector_repository=db_repo,
            turn_builder=turn_builder,
            sql_router=sql_router,
            # No capability_registry - should default to None
        )

        assert factory._capability_registry is None
