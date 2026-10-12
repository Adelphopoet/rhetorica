from __future__ import annotations

from types import SimpleNamespace

from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)
from rhetorica_backend.domain.chat.orchestration.chat_runtime_orchestrator import (
    ChatRuntimeOrchestrator,
)
from rhetorica_backend.domain.results.visualization_planner import VisualizationPlanner


def test_chat_runtime_orchestrator_build_reuses_provided_visualization_planner() -> None:
    planner = VisualizationPlanner()

    runtime = ChatRuntimeOrchestrator.build(
        llm_connector_repository=SimpleNamespace(),
        sql_router=SimpleNamespace(),
        conversation_manager=ConversationManager(chat_repository=None),
        db_connector_repository=SimpleNamespace(),
        rag_retriever=None,
        workspace_skill_repository=None,
        visualization_planner=planner,
        tool_executor_factory=lambda **kwargs: None,
    )

    assert runtime.visualization_planner is planner
    assert runtime.turn_builder is not None
    assert runtime.session_factory is not None
    assert runtime.transport_finalization_input_factory is not None
