"""Tests for typed clarification cards in chat contracts and response assembly."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from rhetorica_backend.contracts.chat import (
    CapabilityTrace,
    ChatAskRequest,
    ChatAskResponse,
    ClarificationChoice,
    ClarificationChoiceType,
    ClarificationRequest,
    DoneEvent,
    OptimizationTrace,
)
from rhetorica_backend.domain.chat.orchestration.response_assembly import (
    AssembledChatTurn,
    ChatResponseAssembler,
)
from rhetorica_backend.domain.results.analysis_document_builder import (
    AnalysisDocumentBuilder,
)
from rhetorica_backend.domain.results.visualization_planner import (
    VisualizationPlanner,
)


class TestClarificationChoice:
    """Tests for ClarificationChoice dataclass."""

    def test_creates_connector_choice(self) -> None:
        """Test creating a connector choice with all fields."""
        choice = ClarificationChoice(
            id="conn-1",
            label="Production Warehouse",
            reason="Primary data warehouse with full history",
            type=ClarificationChoiceType.CHOOSE_CONNECTOR,
            provenance_id="connector-123",
            metadata={"env": "prod"},
        )

        assert choice.id == "conn-1"
        assert choice.label == "Production Warehouse"
        assert choice.reason == "Primary data warehouse with full history"
        assert choice.type == ClarificationChoiceType.CHOOSE_CONNECTOR
        assert choice.provenance_id == "connector-123"
        assert choice.metadata == {"env": "prod"}

    def test_creates_table_choice(self) -> None:
        """Test creating a table choice."""
        choice = ClarificationChoice(
            id="table-1",
            label="dm.orders",
            reason="Contains order transactions with timestamps",
            type=ClarificationChoiceType.CHOOSE_TABLE,
            provenance_id="catalog-456",
        )

        assert choice.type == ClarificationChoiceType.CHOOSE_TABLE
        assert choice.metadata is None

    def test_creates_metric_definition_choice(self) -> None:
        """Test creating a metric definition choice."""
        choice = ClarificationChoice(
            id="metric-1",
            label="Daily Active Users",
            reason="Unique users who logged in on a given day",
            type=ClarificationChoiceType.CHOOSE_METRIC_DEFINITION,
        )

        assert choice.type == ClarificationChoiceType.CHOOSE_METRIC_DEFINITION

    def test_creates_time_range_choice(self) -> None:
        """Test creating a time range choice."""
        choice = ClarificationChoice(
            id="time-1",
            label="Last 30 days",
            reason="Standard reporting period",
            type=ClarificationChoiceType.CHOOSE_TIME_RANGE,
        )

        assert choice.type == ClarificationChoiceType.CHOOSE_TIME_RANGE

    def test_creates_confirm_assumption_choice(self) -> None:
        """Test creating a confirm assumption choice."""
        choice = ClarificationChoice(
            id="assume-1",
            label="Yes, use current month",
            reason="Interpretation of 'current' as current calendar month",
            type=ClarificationChoiceType.CONFIRM_ASSUMPTION,
        )

        assert choice.type == ClarificationChoiceType.CONFIRM_ASSUMPTION

    def test_creates_approve_expensive_query_choice(self) -> None:
        """Test creating an expensive query approval choice."""
        choice = ClarificationChoice(
            id="costly-1",
            label="Proceed anyway",
            reason="Query may scan ~10M rows but completes in ~30s",
            type=ClarificationChoiceType.APPROVE_EXPENSIVE_QUERY,
        )

        assert choice.type == ClarificationChoiceType.APPROVE_EXPENSIVE_QUERY

    def test_validates_required_fields(self) -> None:
        """Test that required fields are validated."""
        with pytest.raises(ValueError):
            ClarificationChoice(
                id="",  # empty id should fail
                label="Test",
                reason="Test reason",
                type=ClarificationChoiceType.CHOOSE_CONNECTOR,
            )

    def test_all_clarification_types_valid(self) -> None:
        """Test that all clarification types are valid enum values."""
        for choice_type in ClarificationChoiceType:
            choice = ClarificationChoice(
                id="test-id",
                label="Test",
                reason="Test reason",
                type=choice_type,
            )
            assert choice.type == choice_type


class TestClarificationRequest:
    """Tests for ClarificationRequest dataclass."""

    def test_creates_request_with_choices(self) -> None:
        """Test creating a clarification request with multiple choices."""
        choices = [
            ClarificationChoice(
                id="conn-1",
                label="Warehouse A",
                reason="Primary warehouse",
                type=ClarificationChoiceType.CHOOSE_CONNECTOR,
            ),
            ClarificationChoice(
                id="conn-2",
                label="Warehouse B",
                reason="Replica warehouse",
                type=ClarificationChoiceType.CHOOSE_CONNECTOR,
            ),
        ]

        request = ClarificationRequest(
            clarification_id="clar-1",
            question="Which warehouse should I query?",
            choices=choices,
            type=ClarificationChoiceType.CHOOSE_CONNECTOR,
        )

        assert request.clarification_id == "clar-1"
        assert len(request.choices) == 2
        assert request.type == ClarificationChoiceType.CHOOSE_CONNECTOR

    def test_requires_at_least_one_choice(self) -> None:
        """Test that a clarification request requires at least one choice."""
        with pytest.raises(ValueError):
            ClarificationRequest(
                clarification_id="clar-1",
                question="Which?",
                choices=[],
                type=ClarificationChoiceType.CHOOSE_CONNECTOR,
            )

    def test_serialization_roundtrip(self) -> None:
        """Test that ClarificationRequest can be serialized and deserialized."""
        original = ClarificationRequest(
            clarification_id="clar-123",
            question="Select a database connector",
            choices=[
                ClarificationChoice(
                    id="opt-1",
                    label="Production",
                    reason="Main production database",
                    type=ClarificationChoiceType.CHOOSE_CONNECTOR,
                    provenance_id="prod-id",
                ),
            ],
            type=ClarificationChoiceType.CHOOSE_CONNECTOR,
        )

        # Serialize to dict
        data = original.model_dump(mode="json")

        # Deserialize back
        restored = ClarificationRequest.model_validate(data)

        assert restored.clarification_id == original.clarification_id
        assert restored.question == original.question
        assert len(restored.choices) == len(original.choices)
        assert restored.choices[0].id == original.choices[0].id
        assert restored.type == original.type


class TestClarificationRequestInChatAskRequest:
    """Tests for selected_clarification_id and selected_clarification_value in requests."""

    def test_chat_ask_request_accepts_clarification_params(self) -> None:
        """Test that ChatAskRequest accepts clarification parameters."""
        request = ChatAskRequest(
            question="Show me sales",
            workspace_id="ws-1",
            selected_clarification_id="clar-1",
            selected_clarification_value="conn-1",
        )

        assert request.selected_clarification_id == "clar-1"
        assert request.selected_clarification_value == "conn-1"

    def test_chat_ask_request_clarification_params_optional(self) -> None:
        """Test that clarification parameters are optional."""
        request = ChatAskRequest(
            question="Show me sales",
            workspace_id="ws-1",
        )

        assert request.selected_clarification_id is None
        assert request.selected_clarification_value is None

    def test_analyst_request_accepts_clarification_params(self) -> None:
        """Test that AnalystChatAskRequest accepts clarification parameters."""
        from rhetorica_backend.contracts.chat import AnalystChatAskRequest

        request = AnalystChatAskRequest(
            question="Show me sales",
            workspace_id="ws-1",
            selected_clarification_id="clar-1",
            selected_clarification_value="table-1",
        )

        assert request.selected_clarification_id == "clar-1"
        assert request.selected_clarification_value == "table-1"


class TestClarificationRequestInResponses:
    """Tests for clarification_request in response types."""

    def test_chat_ask_response_with_clarification_request(self) -> None:
        """Test that ChatAskResponse can include a clarification request."""
        clarification = ClarificationRequest(
            clarification_id="clar-123",
            question="Which database should I use?",
            choices=[
                ClarificationChoice(
                    id="opt-1",
                    label="Production DB",
                    reason="Full dataset",
                    type=ClarificationChoiceType.CHOOSE_CONNECTOR,
                ),
            ],
            type=ClarificationChoiceType.CHOOSE_CONNECTOR,
        )

        response = ChatAskResponse(
            answer="I need to know which database to query.",
            clarification_request=clarification,
        )

        assert response.clarification_request is not None
        assert response.clarification_request.clarification_id == "clar-123"
        assert len(response.clarification_request.choices) == 1

    def test_done_event_with_clarification_request(self) -> None:
        """Test that DoneEvent can include a clarification request."""
        clarification = ClarificationRequest(
            clarification_id="clar-456",
            question="Which table contains the orders?",
            choices=[
                ClarificationChoice(
                    id="opt-1",
                    label="dm.orders",
                    reason="Denormalized orders table",
                    type=ClarificationChoiceType.CHOOSE_TABLE,
                ),
                ClarificationChoice(
                    id="opt-2",
                    label="stg.orders_raw",
                    reason="Raw staging table",
                    type=ClarificationChoiceType.CHOOSE_TABLE,
                ),
            ],
            type=ClarificationChoiceType.CHOOSE_TABLE,
        )

        event = DoneEvent(
            answer="Which table should I use?",
            clarification_request=clarification,
        )

        assert event.clarification_request is not None
        assert len(event.clarification_request.choices) == 2

    def test_response_without_clarification_is_valid(self) -> None:
        """Test that responses without clarification_request are valid."""
        response = ChatAskResponse(answer="Here is your data.")

        assert response.clarification_request is None


class TestAssembledChatTurnWithClarification:
    """Tests for AssembledChatTurn with clarification_request."""

    def test_assembled_turn_with_clarification_request(self) -> None:
        """Test that AssembledChatTurn can include a clarification request."""
        clarification = ClarificationRequest(
            clarification_id=f"clar-{uuid.uuid4().hex[:8]}",
            question="Which warehouse should I connect to?",
            choices=[
                ClarificationChoice(
                    id="wh-1",
                    label="Production Warehouse",
                    reason="Full history available",
                    type=ClarificationChoiceType.CHOOSE_CONNECTOR,
                ),
            ],
            type=ClarificationChoiceType.CHOOSE_CONNECTOR,
        )

        turn = AssembledChatTurn(
            answer="Please clarify which warehouse to use.",
            generated_sql=None,
            selected_database_connector=None,
            metadata_sources=[],
            context_tables=[],
            runtime_context={},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=["needs_user_clarification"],
            capability_trace=CapabilityTrace(),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
            clarification_request=clarification,
        )

        assert turn.clarification_request is not None
        assert turn.clarification_request.type == ClarificationChoiceType.CHOOSE_CONNECTOR

    def test_assembled_turn_without_clarification_is_valid(self) -> None:
        """Test that AssembledChatTurn without clarification is valid."""
        turn = AssembledChatTurn(
            answer="Here is your answer.",
            generated_sql="SELECT 1",
            selected_database_connector="warehouse",
            metadata_sources=["openmetadata"],
            context_tables=["dm.orders"],
            runtime_context={},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=[],
            capability_trace=CapabilityTrace(),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
        )

        assert turn.clarification_request is None


class TestChatResponseAssemblerWithClarification:
    """Tests for ChatResponseAssembler handling clarification_request."""

    def test_assemble_turn_extracts_clarification_request(self) -> None:
        """Test that assemble_turn extracts clarification_request from llm_payload."""
        saved_messages: list[dict[str, object]] = []

        def save_message(**kwargs):
            saved_messages.append(kwargs)
            if kwargs["role"] == "assistant":
                return SimpleNamespace(id="assistant-1")
            return SimpleNamespace(id="user-1")

        assembler = ChatResponseAssembler(
            conversation_manager=SimpleNamespace(save_message=save_message),
            visualization_planner=VisualizationPlanner(),
            analysis_document_builder=AnalysisDocumentBuilder(VisualizationPlanner()),
        )

        clarification_payload = {
            "clarification_id": "clar-test-1",
            "question": "Which database?",
            "choices": [
                {
                    "id": "db-1",
                    "label": "Production",
                    "reason": "Main database",
                    "type": "choose_connector",
                },
            ],
            "type": "choose_connector",
        }

        turn = assembler.assemble_turn(
            llm_payload={
                "answer": "Please clarify your database choice.",
                "clarification_request": clarification_payload,
            },
            warnings=["llm_user_clarification_requested"],
            capability_surface=SimpleNamespace(
                available_tools=[],
                degraded_capabilities=[],
                policy_decisions=[],
            ),
            selected_connector_name=None,
        )

        assert turn.clarification_request is not None
        assert turn.clarification_request.clarification_id == "clar-test-1"
        assert len(turn.clarification_request.choices) == 1
        assert turn.clarification_request.choices[0].id == "db-1"

    def test_to_chat_response_includes_clarification_request(self) -> None:
        """Test that to_chat_response includes clarification_request."""
        clarification = ClarificationRequest(
            clarification_id="clar-1",
            question="Select connector",
            choices=[
                ClarificationChoice(
                    id="c1",
                    label="A",
                    reason="First",
                    type=ClarificationChoiceType.CHOOSE_CONNECTOR,
                ),
            ],
            type=ClarificationChoiceType.CHOOSE_CONNECTOR,
        )

        turn = AssembledChatTurn(
            answer="Clarify",
            generated_sql=None,
            selected_database_connector=None,
            metadata_sources=[],
            context_tables=[],
            runtime_context={},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=["needs_user_clarification"],
            capability_trace=CapabilityTrace(),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
            clarification_request=clarification,
        )

        response = turn.to_chat_response(
            conversation_id="conv-1",
            assistant_message_id="msg-1",
        )

        assert response.clarification_request is not None
        assert response.clarification_request.clarification_id == "clar-1"

    def test_to_done_event_includes_clarification_request(self) -> None:
        """Test that to_done_event includes clarification_request."""
        clarification = ClarificationRequest(
            clarification_id="clar-2",
            question="Select table",
            choices=[
                ClarificationChoice(
                    id="t1",
                    label="orders",
                    reason="Order table",
                    type=ClarificationChoiceType.CHOOSE_TABLE,
                ),
            ],
            type=ClarificationChoiceType.CHOOSE_TABLE,
        )

        turn = AssembledChatTurn(
            answer="Clarify",
            generated_sql=None,
            selected_database_connector=None,
            metadata_sources=[],
            context_tables=[],
            runtime_context={},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=["needs_user_clarification"],
            capability_trace=CapabilityTrace(),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
            clarification_request=clarification,
        )

        event = turn.to_done_event(
            conversation_id="conv-1",
            assistant_message_id="msg-1",
            cancelled=False,
        )

        assert event.clarification_request is not None
        assert event.clarification_request.clarification_id == "clar-2"
