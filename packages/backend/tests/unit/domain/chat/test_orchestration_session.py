from __future__ import annotations

from types import SimpleNamespace

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.session import (
    OrchestrationSessionFactory,
)
from rhetorica_backend.domain.chat.orchestration.turn_builder import (
    WorkspaceToolingSnapshot,
)


def test_session_factory_builds_history_and_capability_surface() -> None:
    seen_history_kwargs: dict[str, object] = {}

    def format_history_for_context(**kwargs):
        seen_history_kwargs.update(kwargs)
        return ([{"role": "assistant", "content": "prev"}], ["history_trimmed"])

    factory = OrchestrationSessionFactory(
        llm_connector_repository=SimpleNamespace(
            list=lambda **_kwargs: [SimpleNamespace(name="gpt-main")],
            get_connection=lambda **_kwargs: SimpleNamespace(
                model="gpt-4.1",
                extra_settings={"context_window_tokens": 8192},
            ),
        ),
        conversation_manager=SimpleNamespace(
            create_conversation=lambda **_kwargs: SimpleNamespace(id="conv-1"),
            format_history_for_context=format_history_for_context,
        ),
        db_connector_repository=SimpleNamespace(
            list=lambda **_kwargs: [SimpleNamespace(name="warehouse")]
        ),
        turn_builder=SimpleNamespace(
            inspect_workspace=lambda **_kwargs: WorkspaceToolingSnapshot(
                has_db_connectors=True,
                metadata_configured=True,
                metadata_connector_names=["ometa"],
                rag_configured=False,
                confluence_configured=False,
                confluence_connector_names=[],
                jira_configured=False,
                jira_connector_names=[],
            )
        ),
        sql_router=SimpleNamespace(
            is_non_analytic_question=lambda _question: False
        ),
    )

    session = factory.build(
        ChatAskRequest(question="Покажи выручку по дням", workspace_id="ws-1")
    )

    assert session.conversation_id == "conv-1"
    assert session.conversation_history == [{"role": "assistant", "content": "prev"}]
    assert session.warnings == ["history_trimmed"]
    assert session.workspace_snapshot.metadata_connector_names == ["ometa"]
    assert "fetch_metadata_tables" in session.capability_surface.available_tools
    assert seen_history_kwargs["model_context_window_tokens"] == 8192

