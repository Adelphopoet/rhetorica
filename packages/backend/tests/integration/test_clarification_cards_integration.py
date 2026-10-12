"""Integration tests for typed clarification cards in chat orchestration flow."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from rhetorica_backend.contracts.chat import (
    CapabilityTrace,
    ChatAskRequest,
    ClarificationChoice,
    ClarificationChoiceType,
    ClarificationRequest,
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


def _create_llm_payload_with_typed_clarification(
    clarification_id: str,
    question: str,
    choices: list[dict[str, Any]],
    clarification_type: str,
) -> dict[str, Any]:
    """Helper to create LLM payload with typed clarification request."""
    return {
        "answer": question,
        "answer_markdown": question,
        "uncertainty_flags": ["needs_user_clarification"],
        "clarification_request": {
            "clarification_id": clarification_id,
            "question": question,
            "choices": choices,
            "type": clarification_type,
        },
        "runtime_context": {},
        "metadata_sources": [],
        "context_tables": [],
        "claims": [],
        "evidence": [],
        "claim_evidence_links": [],
        "capability_trace": {"available_tools": [], "used_tools": [], "used_providers": []},
        "optimization_trace": {},
    }


class TestAmbiguousConnectorTypedClarification:
    """Integration tests for ambiguous connector scenario returning typed clarification."""

    def test_ambiguous_connector_returns_typed_clarification(self) -> None:
        """Test that ambiguous connector query returns typed clarification choice."""
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

        # Simulate LLM returning typed clarification for ambiguous connector
        llm_payload = _create_llm_payload_with_typed_clarification(
            clarification_id="clar-conn-001",
            question="Which database should I query?",
            choices=[
                {
                    "id": "warehouse-prod",
                    "label": "Production Warehouse",
                    "reason": "Primary data warehouse with full history",
                    "type": "choose_connector",
                    "provenance_id": "conn-prod-123",
                },
                {
                    "id": "warehouse-dev",
                    "label": "Development Warehouse",
                    "reason": "Sandbox environment for testing",
                    "type": "choose_connector",
                    "provenance_id": "conn-dev-456",
                },
            ],
            clarification_type="choose_connector",
        )

        capability_surface = SimpleNamespace(
            available_tools=["run_sql", "catalog__tables"],
            degraded_capabilities=[],
            policy_decisions=[],
        )

        turn = assembler.assemble_turn(
            llm_payload=llm_payload,
            warnings=["llm_user_clarification_requested"],
            capability_surface=capability_surface,
            selected_connector_name=None,
        )

        # Verify typed clarification was extracted
        assert turn.clarification_request is not None
        assert turn.clarification_request.type == ClarificationChoiceType.CHOOSE_CONNECTOR
        assert turn.clarification_request.clarification_id == "clar-conn-001"
        assert len(turn.clarification_request.choices) == 2

        # Verify first choice
        first_choice = turn.clarification_request.choices[0]
        assert first_choice.id == "warehouse-prod"
        assert first_choice.label == "Production Warehouse"
        assert first_choice.type == ClarificationChoiceType.CHOOSE_CONNECTOR
        assert first_choice.provenance_id == "conn-prod-123"

        # Verify uncertainty flag is still present
        assert "needs_user_clarification" in turn.uncertainty_flags

    def test_ambiguous_table_returns_typed_clarification(self) -> None:
        """Test that ambiguous table query returns typed clarification for table choice."""
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

        llm_payload = _create_llm_payload_with_typed_clarification(
            clarification_id="clar-table-001",
            question="Which table contains the orders data?",
            choices=[
                {
                    "id": "dm-orders",
                    "label": "dm.orders",
                    "reason": "Denormalized orders with customer join",
                    "type": "choose_table",
                    "provenance_id": "table-orders-dm",
                },
                {
                    "id": "stg-orders",
                    "label": "stg.orders_raw",
                    "reason": "Raw staging table before transformation",
                    "type": "choose_table",
                    "provenance_id": "table-orders-stg",
                },
            ],
            clarification_type="choose_table",
        )

        capability_surface = SimpleNamespace(
            available_tools=["run_sql", "catalog__tables"],
            degraded_capabilities=[],
            policy_decisions=[],
        )

        turn = assembler.assemble_turn(
            llm_payload=llm_payload,
            warnings=["llm_user_clarification_requested"],
            capability_surface=capability_surface,
            selected_connector_name="warehouse",
        )

        assert turn.clarification_request is not None
        assert turn.clarification_request.type == ClarificationChoiceType.CHOOSE_TABLE
        assert len(turn.clarification_request.choices) == 2

        # Verify table choices have correct structure
        for choice in turn.clarification_request.choices:
            assert choice.type == ClarificationChoiceType.CHOOSE_TABLE
            assert choice.id in ("dm-orders", "stg-orders")

    def test_expensive_query_approval_typed_clarification(self) -> None:
        """Test that expensive query returns approval required clarification."""
        assembler = ChatResponseAssembler(
            conversation_manager=SimpleNamespace(save_message=lambda **_: SimpleNamespace(id="x")),
            visualization_planner=VisualizationPlanner(),
            analysis_document_builder=AnalysisDocumentBuilder(VisualizationPlanner()),
        )

        llm_payload = _create_llm_payload_with_typed_clarification(
            clarification_id="clar-cost-001",
            question="This query may be expensive. Do you want to proceed?",
            choices=[
                {
                    "id": "approve",
                    "label": "Proceed anyway",
                    "reason": "Query will scan ~50M rows but is necessary for analysis",
                    "type": "approve_expensive_query",
                },
                {
                    "id": "cancel",
                    "label": "Cancel query",
                    "reason": "Abort to avoid long-running query",
                    "type": "approve_expensive_query",
                },
            ],
            clarification_type="approve_expensive_query",
        )

        capability_surface = SimpleNamespace(
            available_tools=["run_sql"],
            degraded_capabilities=[],
            policy_decisions=[],
        )

        turn = assembler.assemble_turn(
            llm_payload=llm_payload,
            warnings=["llm_expensive_query_detected", "llm_user_clarification_requested"],
            capability_surface=capability_surface,
            selected_connector_name="warehouse",
        )

        assert turn.clarification_request is not None
        assert turn.clarification_request.type == ClarificationChoiceType.APPROVE_EXPENSIVE_QUERY
        assert len(turn.clarification_request.choices) == 2

        approve_choice = next(c for c in turn.clarification_request.choices if c.id == "approve")
        assert approve_choice.type == ClarificationChoiceType.APPROVE_EXPENSIVE_QUERY

    def test_confirm_assumption_typed_clarification(self) -> None:
        """Test that assumption confirmation returns typed clarification."""
        assembler = ChatResponseAssembler(
            conversation_manager=SimpleNamespace(save_message=lambda **_: SimpleNamespace(id="x")),
            visualization_planner=VisualizationPlanner(),
            analysis_document_builder=AnalysisDocumentBuilder(VisualizationPlanner()),
        )

        llm_payload = _create_llm_payload_with_typed_clarification(
            clarification_id="clar-assume-001",
            question="Should I use the current month (May 2026) for the analysis?",
            choices=[
                {
                    "id": "current-month",
                    "label": "Yes, use current month",
                    "reason": "Interpretation of 'current' as current calendar month",
                    "type": "confirm_assumption",
                },
                {
                    "id": "previous-month",
                    "label": "No, use previous month",
                    "reason": "Analysis should cover April 2026",
                    "type": "confirm_assumption",
                },
            ],
            clarification_type="confirm_assumption",
        )

        capability_surface = SimpleNamespace(
            available_tools=["run_sql"],
            degraded_capabilities=[],
            policy_decisions=[],
        )

        turn = assembler.assemble_turn(
            llm_payload=llm_payload,
            warnings=["llm_user_clarification_requested"],
            capability_surface=capability_surface,
            selected_connector_name="warehouse",
        )

        assert turn.clarification_request is not None
        assert turn.clarification_request.type == ClarificationChoiceType.CONFIRM_ASSUMPTION

    def test_response_includes_clarification_for_ui_card_rendering(self) -> None:
        """Test that final response includes all fields needed for UI card rendering."""
        clarification = ClarificationRequest(
            clarification_id="clar-ui-001",
            question="Which data source should I use?",
            choices=[
                ClarificationChoice(
                    id="source-a",
                    label="Source A",
                    reason="Primary production data",
                    type=ClarificationChoiceType.CHOOSE_CONNECTOR,
                    provenance_id="conn-a",
                    metadata={"env": "prod", "priority": 1},
                ),
            ],
            type=ClarificationChoiceType.CHOOSE_CONNECTOR,
        )

        turn = AssembledChatTurn(
            answer="Please select a data source.",
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

        # Get ChatAskResponse
        response = turn.to_chat_response(
            conversation_id="conv-123",
            assistant_message_id="msg-456",
        )

        # Verify response contains all fields for UI card rendering
        assert response.clarification_request is not None
        assert response.clarification_request.clarification_id == "clar-ui-001"
        assert response.clarification_request.question == "Which data source should I use?"
        assert len(response.clarification_request.choices) == 1

        choice = response.clarification_request.choices[0]
        assert choice.id == "source-a"
        assert choice.label == "Source A"
        assert choice.reason == "Primary production data"
        assert choice.type == ClarificationChoiceType.CHOOSE_CONNECTOR
        assert choice.provenance_id == "conn-a"
        assert choice.metadata == {"env": "prod", "priority": 1}

        # Verify uncertainty flag is in response
        assert "needs_user_clarification" in response.uncertainty_flags

    def test_typed_clarification_roundtrip_through_response(self) -> None:
        """Test that typed clarification survives full serialization roundtrip."""
        original = ClarificationRequest(
            clarification_id="clar-roundtrip-001",
            question="Select a connector",
            choices=[
                ClarificationChoice(
                    id="conn-1",
                    label="Primary",
                    reason="Main database",
                    type=ClarificationChoiceType.CHOOSE_CONNECTOR,
                    provenance_id="p1",
                ),
                ClarificationChoice(
                    id="conn-2",
                    label="Secondary",
                    reason="Backup database",
                    type=ClarificationChoiceType.CHOOSE_CONNECTOR,
                    provenance_id="p2",
                ),
            ],
            type=ClarificationChoiceType.CHOOSE_CONNECTOR,
        )

        turn = AssembledChatTurn(
            answer="Please select",
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
            clarification_request=original,
        )

        # Serialize through ChatAskResponse
        response = turn.to_chat_response(
            conversation_id="conv-1",
            assistant_message_id="msg-1",
        )

        # Deserialize
        response_dict = response.model_dump(mode="json")

        # Verify roundtrip
        assert response_dict["clarification_request"]["clarification_id"] == "clar-roundtrip-001"
        assert len(response_dict["clarification_request"]["choices"]) == 2
        assert response_dict["clarification_request"]["type"] == "choose_connector"


class TestClarificationSelectionInRequest:
    """Tests for handling selected_clarification_id in subsequent requests."""

    def test_request_with_clarification_selection(self) -> None:
        """Test that user can send clarification selection in request."""
        request = ChatAskRequest(
            question="Show me sales data",
            workspace_id="ws-1",
            conversation_id="conv-1",
            selected_clarification_id="clar-001",
            selected_clarification_value="conn-prod",
        )

        assert request.selected_clarification_id == "clar-001"
        assert request.selected_clarification_value == "conn-prod"

    def test_request_with_time_range_clarification_selection(self) -> None:
        """Test that user can select a time range clarification."""
        request = ChatAskRequest(
            question="Show me DAU",
            workspace_id="ws-1",
            selected_clarification_id="clar-time-001",
            selected_clarification_value="last_30_days",
        )

        assert request.selected_clarification_id == "clar-time-001"
        assert request.selected_clarification_value == "last_30_days"

    def test_request_without_selection_is_valid(self) -> None:
        """Test that regular requests without clarification selection are valid."""
        request = ChatAskRequest(
            question="Show me all orders",
            workspace_id="ws-1",
        )

        assert request.selected_clarification_id is None
        assert request.selected_clarification_value is None
