from __future__ import annotations

from pathlib import Path

from rhetorica_backend.contracts.admin_integrations import (
    LlmConnectionConfig,
    RagConnectionConfig,
    UpsertLlmConnectorRequest,
    UpsertRagConnectorRequest,
)
from rhetorica_backend.domain.rag import pipeline_service as pipeline_module
from rhetorica_backend.domain.rag.models import (
    RagGateDecision,
    RagMergeDecision,
    RagPipelineTaskRequest,
)
from rhetorica_backend.domain.rag.pipeline_service import RagInsightPipelineService
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import InMemorySecretStore


def _runtime(tmp_path: Path, db_name: str) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/{db_name}")
    runtime.create_schema()
    return runtime


def _seed_conversation(
    chat_repo: ChatRepository,
    workspace_id: str = "default",
    warnings: list[str] | None = None,
    user_content: str = "How should I analyze net revenue?",
    assistant_content: str = (
        "Net revenue is gross revenue minus refunds and discounts. "
        "Start with dm.revenue_daily, validate against fct_orders and fct_refunds, "
        "and break the analysis down by channel and country."
    ),
    context_tables: list[str] | None = None,
    generated_sql: str | None = "select 1",
    metadata_sources: list[str] | None = None,
) -> tuple[str, str]:
    conversation = chat_repo.create_conversation(workspace_id=workspace_id, title="RAG test")
    chat_repo.save_message(
        conversation_id=conversation.id,
        role="user",
        content=user_content,
    )
    assistant = chat_repo.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content=assistant_content,
        generated_sql=generated_sql,
        warnings=warnings or [],
        context_tables=context_tables or ["dm.revenue_daily", "fct_orders", "fct_refunds"],
        metadata_sources=metadata_sources or ["openmetadata"],
    )
    updated = chat_repo.update_message_feedback(
        conversation_id=conversation.id,
        message_id=assistant.id,
        feedback="useful",
    )
    assert updated is not None
    return conversation.id, assistant.id


def _seed_connectors(
    runtime: DatabaseRuntime,
    confidence_threshold: float = 0.7,
    include_rag: bool = True,
) -> None:
    llm_repo = LlmConnectorRepository(db=runtime, secret_store=InMemorySecretStore())
    llm_repo.upsert(
        UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="default",
            connection=LlmConnectionConfig(
                base_url="https://llm.example/v1",
                model="gpt-4.1-mini",
                api_key="secret",
            ),
        )
    )
    if include_rag:
        rag_repo = RagConnectorRepository(db=runtime, secret_store=InMemorySecretStore())
        rag_repo.upsert(
            UpsertRagConnectorRequest(
                name="rag-main",
                workspace_id="default",
                connection=RagConnectionConfig(
                    provider="qdrant",
                    base_url="http://qdrant:6333",
                    api_key="secret",
                    collection_name="rag_insights",
                    vector_size=8,
                    distance="cosine",
                    embedding_model="BAAI/bge-small-en-v1.5",
                    confidence_threshold=confidence_threshold,
                ),
            )
        )


def test_pipeline_hard_gate_rejects_blocking_warnings(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "rag_pipeline_hard_gate.db")
    chat_repo = ChatRepository(db=runtime)
    conversation_id, message_id = _seed_conversation(
        chat_repo=chat_repo,
        warnings=["unsafe_output_detected"],
    )

    service = RagInsightPipelineService(
        chat_repository=chat_repo,
        llm_connector_repository=LlmConnectorRepository(db=runtime),
        rag_connector_repository=RagConnectorRepository(db=runtime),
    )
    result = service.process_feedback(
        RagPipelineTaskRequest(
            workspace_id="default",
            conversation_id=conversation_id,
            message_id=message_id,
        )
    )
    assert result.status == "skipped"
    assert result.reason == "hard_gate_rejected"


def test_pipeline_rejects_ephemeral_fact_before_llm_gate(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, "rag_pipeline_ephemeral_fact.db")
    chat_repo = ChatRepository(db=runtime)
    conversation_id, message_id = _seed_conversation(
        chat_repo=chat_repo,
        user_content="What was net revenue for March?",
        assistant_content="За март net_revenue составляет 12.4 млн ₽.",
        context_tables=["dm.revenue_monthly"],
    )
    _seed_connectors(runtime)

    def _unexpected_llm_gate(*args, **kwargs) -> RagGateDecision:
        raise AssertionError("LLM gate should not run for obvious ephemeral facts")

    monkeypatch.setattr(RagInsightPipelineService, "_run_llm_gate", _unexpected_llm_gate)

    service = RagInsightPipelineService(
        chat_repository=chat_repo,
        llm_connector_repository=LlmConnectorRepository(db=runtime),
        rag_connector_repository=RagConnectorRepository(db=runtime),
    )
    result = service.process_feedback(
        RagPipelineTaskRequest(
            workspace_id="default",
            conversation_id=conversation_id,
            message_id=message_id,
        )
    )
    assert result.status == "skipped"
    assert result.reason == "deterministic_gate_ephemeral_fact"


def test_pipeline_stores_pending_insight_with_mock_qdrant(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, "rag_pipeline_store.db")
    chat_repo = ChatRepository(db=runtime)
    conversation_id, message_id = _seed_conversation(chat_repo=chat_repo)
    _seed_connectors(runtime)

    captured: dict[str, object] = {}

    class _FakeStore:
        def __init__(self, _connection):
            pass

        def search_similar(
            self,
            workspace_id: str,
            vector: list[float],
            limit: int,
            score_threshold: float,
        ) -> list[dict[str, object]]:
            return []

        def upsert(self, point_id: str, vector: list[float], payload: dict[str, object]) -> None:
            captured["point_id"] = point_id
            captured["vector"] = vector
            captured["payload"] = payload

        def update_payload(self, point_id: str, payload: dict[str, object]) -> None:
            captured["updated_point_id"] = point_id
            captured["updated_payload"] = payload

    class _FakeEmbedder:
        def __init__(self, _model_name: str):
            pass

        def embed(self, _text: str) -> list[float]:
            return [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

    monkeypatch.setattr(pipeline_module, "QdrantInsightsStore", _FakeStore)
    monkeypatch.setattr(pipeline_module, "FastEmbedProvider", _FakeEmbedder)
    monkeypatch.setattr(
        RagInsightPipelineService,
        "_run_llm_gate",
        lambda self, llm_client, user_question, message: RagGateDecision(
            should_store=True,
            confidence=0.91,
            reason="highly_reusable",
            compact_summary="Weekday morning campaign improved retention by 12%.",
            keywords=["weekday", "retention", "campaign"],
        ),
    )

    service = RagInsightPipelineService(
        chat_repository=chat_repo,
        llm_connector_repository=LlmConnectorRepository(db=runtime),
        rag_connector_repository=RagConnectorRepository(db=runtime),
    )
    result = service.process_feedback(
        RagPipelineTaskRequest(
            workspace_id="default",
            conversation_id=conversation_id,
            message_id=message_id,
        )
    )
    assert result.status == "stored"
    assert result.point_id is not None
    assert captured["point_id"] == result.point_id
    assert captured["vector"] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["workspace_id"] == "default"
    assert payload["conversation_id"] == conversation_id
    assert payload["message_id"] == message_id
    assert payload["verification_status"] == "pending"
    assert payload["human_verified"] is False
    assert payload["llm_confidence"] == 0.91
    assert isinstance(payload["article_markdown"], str)
    assert str(payload["article_markdown"]).startswith("#")
    assert payload["likes_count"] == 1
    assert payload["liked_message_ids"] == [message_id]


def test_pipeline_skips_when_confidence_below_threshold(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, "rag_pipeline_confidence.db")
    chat_repo = ChatRepository(db=runtime)
    conversation_id, message_id = _seed_conversation(chat_repo=chat_repo)
    _seed_connectors(runtime, confidence_threshold=0.95)

    monkeypatch.setattr(
        RagInsightPipelineService,
        "_run_llm_gate",
        lambda self, llm_client, user_question, message: RagGateDecision(
            should_store=True,
            confidence=0.62,
            reason="weak_signal",
            compact_summary="Weak confidence summary.",
            keywords=["weak"],
        ),
    )

    service = RagInsightPipelineService(
        chat_repository=chat_repo,
        llm_connector_repository=LlmConnectorRepository(db=runtime),
        rag_connector_repository=RagConnectorRepository(db=runtime),
    )
    result = service.process_feedback(
        RagPipelineTaskRequest(
            workspace_id="default",
            conversation_id=conversation_id,
            message_id=message_id,
        )
    )
    assert result.status == "skipped"
    assert result.reason == "llm_confidence_below_threshold"


def test_pipeline_uses_builtin_rag_when_db_connector_missing(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, "rag_pipeline_builtin.db")
    chat_repo = ChatRepository(db=runtime)
    conversation_id, message_id = _seed_conversation(chat_repo=chat_repo)
    _seed_connectors(runtime, include_rag=False)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("RAG_COLLECTION_NAME", "rag_insights")
    monkeypatch.setenv("RAG_VECTOR_SIZE", "8")

    captured: dict[str, object] = {}

    class _FakeStore:
        def __init__(self, connection):
            captured["connection_base_url"] = connection.base_url
            captured["connection_collection"] = connection.collection_name

        def search_similar(
            self,
            workspace_id: str,
            vector: list[float],
            limit: int,
            score_threshold: float,
        ) -> list[dict[str, object]]:
            return []

        def upsert(self, point_id: str, vector: list[float], payload: dict[str, object]) -> None:
            captured["point_id"] = point_id
            captured["payload"] = payload

        def update_payload(self, point_id: str, payload: dict[str, object]) -> None:
            captured["updated_point_id"] = point_id
            captured["updated_payload"] = payload

    class _FakeEmbedder:
        def __init__(self, _model_name: str):
            pass

        def embed(self, _text: str) -> list[float]:
            return [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

    monkeypatch.setattr(pipeline_module, "QdrantInsightsStore", _FakeStore)
    monkeypatch.setattr(pipeline_module, "FastEmbedProvider", _FakeEmbedder)
    monkeypatch.setattr(
        RagInsightPipelineService,
        "_run_llm_gate",
        lambda self, llm_client, user_question, message: RagGateDecision(
            should_store=True,
            confidence=0.9,
            reason="reusable",
            compact_summary="Built-in connector summary.",
            keywords=["built-in"],
        ),
    )

    service = RagInsightPipelineService(
        chat_repository=chat_repo,
        llm_connector_repository=LlmConnectorRepository(db=runtime),
        rag_connector_repository=RagConnectorRepository(db=runtime),
    )
    result = service.process_feedback(
        RagPipelineTaskRequest(
            workspace_id="default",
            conversation_id=conversation_id,
            message_id=message_id,
        )
    )
    assert result.status == "stored"
    assert result.reason == "ok"
    assert captured["connection_base_url"] == "http://qdrant:6333"
    assert captured["connection_collection"] == "rag_insights"


def test_pipeline_merges_into_similar_document(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, "rag_pipeline_merge.db")
    chat_repo = ChatRepository(db=runtime)
    conversation_id, message_id = _seed_conversation(chat_repo=chat_repo)
    _seed_connectors(runtime)

    captured: dict[str, object] = {}

    class _FakeStore:
        def __init__(self, _connection):
            pass

        def search_similar(
            self,
            workspace_id: str,
            vector: list[float],
            limit: int,
            score_threshold: float,
        ) -> list[dict[str, object]]:
            return [
                {
                    "id": "existing-point",
                    "score": 0.96,
                    "payload": {
                        "workspace_id": "default",
                        "summary": "Existing summary",
                        "keywords": ["old"],
                        "context_tables": ["dm.old"],
                        "metadata_sources": ["openmetadata"],
                        "generated_sql": "select 1",
                        "likes_count": 2,
                        "liked_message_ids": [],
                    },
                }
            ]

        def upsert(self, point_id: str, vector: list[float], payload: dict[str, object]) -> None:
            captured["upsert_called"] = True

        def update_payload(self, point_id: str, payload: dict[str, object]) -> None:
            captured["point_id"] = point_id
            captured["payload"] = payload

    class _FakeEmbedder:
        def __init__(self, _model_name: str):
            pass

        def embed(self, _text: str) -> list[float]:
            return [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

    monkeypatch.setattr(pipeline_module, "QdrantInsightsStore", _FakeStore)
    monkeypatch.setattr(pipeline_module, "FastEmbedProvider", _FakeEmbedder)
    monkeypatch.setattr(
        RagInsightPipelineService,
        "_run_llm_gate",
        lambda self, llm_client, user_question, message: RagGateDecision(
            should_store=True,
            confidence=0.93,
            reason="strong",
            compact_summary="New summary",
            keywords=["new", "insight"],
        ),
    )
    monkeypatch.setattr(
        RagInsightPipelineService,
        "_run_merge_decision",
        lambda self, llm_client, existing_payload, new_decision, message: RagMergeDecision(
            should_update_existing=True,
            reason="has_new_info",
            updated_summary="Merged summary",
            updated_keywords=["old", "new"],
        ),
    )

    service = RagInsightPipelineService(
        chat_repository=chat_repo,
        llm_connector_repository=LlmConnectorRepository(db=runtime),
        rag_connector_repository=RagConnectorRepository(db=runtime),
    )
    result = service.process_feedback(
        RagPipelineTaskRequest(
            workspace_id="default",
            conversation_id=conversation_id,
            message_id=message_id,
        )
    )
    assert result.status == "stored"
    assert result.reason == "merged_into_existing"
    assert result.point_id == "existing-point"
    assert captured.get("upsert_called") is None

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["summary"] == "Merged summary"
    assert payload["likes_count"] == 3
    assert payload["liked_message_ids"] == [message_id]
    assert payload["verification_status"] == "pending"


def test_pipeline_links_existing_like_without_merge(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, "rag_pipeline_link.db")
    chat_repo = ChatRepository(db=runtime)
    conversation_id, message_id = _seed_conversation(chat_repo=chat_repo)
    _seed_connectors(runtime)

    captured: dict[str, object] = {}

    class _FakeStore:
        def __init__(self, _connection):
            pass

        def search_similar(
            self,
            workspace_id: str,
            vector: list[float],
            limit: int,
            score_threshold: float,
        ) -> list[dict[str, object]]:
            return [
                {
                    "id": "existing-point",
                    "score": 0.97,
                    "payload": {
                        "workspace_id": "default",
                        "summary": "Existing summary",
                        "keywords": ["old"],
                        "likes_count": 5,
                        "liked_message_ids": ["other-message"],
                    },
                }
            ]

        def upsert(self, point_id: str, vector: list[float], payload: dict[str, object]) -> None:
            captured["upsert_called"] = True

        def update_payload(self, point_id: str, payload: dict[str, object]) -> None:
            captured["point_id"] = point_id
            captured["payload"] = payload

    class _FakeEmbedder:
        def __init__(self, _model_name: str):
            pass

        def embed(self, _text: str) -> list[float]:
            return [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

    monkeypatch.setattr(pipeline_module, "QdrantInsightsStore", _FakeStore)
    monkeypatch.setattr(pipeline_module, "FastEmbedProvider", _FakeEmbedder)
    monkeypatch.setattr(
        RagInsightPipelineService,
        "_run_llm_gate",
        lambda self, llm_client, user_question, message: RagGateDecision(
            should_store=True,
            confidence=0.9,
            reason="reuse",
            compact_summary="Fresh summary",
            keywords=["fresh"],
        ),
    )
    monkeypatch.setattr(
        RagInsightPipelineService,
        "_run_merge_decision",
        lambda self, llm_client, existing_payload, new_decision, message: RagMergeDecision(
            should_update_existing=False,
            reason="no_new_info",
            updated_summary="Existing summary",
            updated_keywords=["old"],
        ),
    )

    service = RagInsightPipelineService(
        chat_repository=chat_repo,
        llm_connector_repository=LlmConnectorRepository(db=runtime),
        rag_connector_repository=RagConnectorRepository(db=runtime),
    )
    result = service.process_feedback(
        RagPipelineTaskRequest(
            workspace_id="default",
            conversation_id=conversation_id,
            message_id=message_id,
        )
    )
    assert result.status == "stored"
    assert result.reason == "linked_existing_like"
    assert result.point_id == "existing-point"
    assert captured.get("upsert_called") is None

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["likes_count"] == 6
    assert payload["liked_message_ids"] == ["other-message", message_id]


def test_pipeline_is_idempotent_for_existing_liked_message(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, "rag_pipeline_idempotent_like.db")
    chat_repo = ChatRepository(db=runtime)
    conversation_id, message_id = _seed_conversation(chat_repo=chat_repo)
    _seed_connectors(runtime)

    captured: dict[str, object] = {}

    class _FakeStore:
        def __init__(self, _connection):
            pass

        def search_similar(
            self,
            workspace_id: str,
            vector: list[float],
            limit: int,
            score_threshold: float,
        ) -> list[dict[str, object]]:
            return [
                {
                    "id": "existing-point",
                    "score": 0.99,
                    "payload": {
                        "workspace_id": "default",
                        "summary": "Existing summary",
                        "likes_count": 8,
                        "liked_message_ids": [message_id],
                    },
                }
            ]

        def upsert(self, point_id: str, vector: list[float], payload: dict[str, object]) -> None:
            captured["upsert_called"] = True

        def update_payload(self, point_id: str, payload: dict[str, object]) -> None:
            captured["updated_point_id"] = point_id
            captured["updated_payload"] = payload

    class _FakeEmbedder:
        def __init__(self, _model_name: str):
            pass

        def embed(self, _text: str) -> list[float]:
            return [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

    monkeypatch.setattr(pipeline_module, "QdrantInsightsStore", _FakeStore)
    monkeypatch.setattr(pipeline_module, "FastEmbedProvider", _FakeEmbedder)
    monkeypatch.setattr(
        RagInsightPipelineService,
        "_run_llm_gate",
        lambda self, llm_client, user_question, message: RagGateDecision(
            should_store=True,
            confidence=0.94,
            reason="strong",
            compact_summary="Summary",
            keywords=["k"],
        ),
    )

    service = RagInsightPipelineService(
        chat_repository=chat_repo,
        llm_connector_repository=LlmConnectorRepository(db=runtime),
        rag_connector_repository=RagConnectorRepository(db=runtime),
    )
    result = service.process_feedback(
        RagPipelineTaskRequest(
            workspace_id="default",
            conversation_id=conversation_id,
            message_id=message_id,
        )
    )
    assert result.status == "stored"
    assert result.reason == "already_linked_to_existing"
    assert result.point_id == "existing-point"
    assert captured.get("upsert_called") is None
    assert captured.get("updated_payload") is None


def test_parse_json_payload_extracts_json_from_wrapped_text() -> None:
    content = (
        "Sure, here is the evaluation.\n"
        "```json\n"
        '{"should_store": true, "confidence": 0.82, "reason": "useful", '
        '"compact_summary": "summary", "keywords": ["k1"]}\n'
        "```\n"
        "Done."
    )
    parsed = RagInsightPipelineService._parse_json_payload(content)
    assert parsed is not None
    assert parsed["should_store"] is True
    assert parsed["confidence"] == 0.82


def test_parse_json_payload_extracts_braced_object_from_plain_text() -> None:
    content = (
        "Decision: "
        '{"should_update_existing": false, "reason": "duplicate", '
        '"updated_summary": "summary", "updated_keywords": ["old"]}'
    )
    parsed = RagInsightPipelineService._parse_json_payload(content)
    assert parsed is not None
    assert parsed["should_update_existing"] is False
    assert parsed["reason"] == "duplicate"


def test_llm_gate_fallback_stores_useful_answer_when_json_invalid(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "rag_pipeline_gate_fallback_store.db")
    chat_repo = ChatRepository(db=runtime)
    conversation_id, message_id = _seed_conversation(chat_repo=chat_repo)
    message = chat_repo.get_message(conversation_id=conversation_id, message_id=message_id)
    assert message is not None

    class _FakeLlmClient:
        @staticmethod
        def chat_completion(messages: list[dict[str, object]]):
            _ = messages
            return type("Response", (), {"message": {"content": "this is not valid json"}})()

    service = RagInsightPipelineService(
        chat_repository=chat_repo,
        llm_connector_repository=LlmConnectorRepository(db=runtime),
        rag_connector_repository=RagConnectorRepository(db=runtime),
    )
    decision = service._run_llm_gate(
        llm_client=_FakeLlmClient(),
        user_question="Which campaign worked better?",
        message=message,
    )

    assert decision.should_store is True
    assert decision.reason == "invalid_llm_json_fallback"
    assert decision.confidence == 0.72
    assert isinstance(decision.article_markdown, str)
    assert decision.article_markdown.startswith("# Insight")


def test_llm_gate_fallback_skips_trivial_smalltalk_when_json_invalid(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "rag_pipeline_gate_fallback_smalltalk.db")
    chat_repo = ChatRepository(db=runtime)
    conversation = chat_repo.create_conversation(workspace_id="default", title="smalltalk")
    chat_repo.save_message(
        conversation_id=conversation.id,
        role="user",
        content="Привет",
    )
    assistant = chat_repo.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="Привет! Чем могу помочь?",
    )

    class _FakeLlmClient:
        @staticmethod
        def chat_completion(messages: list[dict[str, object]]):
            _ = messages
            return type("Response", (), {"message": {"content": "bad-json"}})()

    service = RagInsightPipelineService(
        chat_repository=chat_repo,
        llm_connector_repository=LlmConnectorRepository(db=runtime),
        rag_connector_repository=RagConnectorRepository(db=runtime),
    )
    decision = service._run_llm_gate(
        llm_client=_FakeLlmClient(),
        user_question="Привет",
        message=assistant,
    )

    assert decision.should_store is False
    assert decision.reason == "invalid_llm_json_fallback"
    assert decision.confidence == 0
