from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from rhetorica_backend.contracts.chat import SemanticGrounding, SemanticGroundingCandidate
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import EngineConnectionConfig, ExecuteSqlResponse
from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.chat_orchestrator import ChatOrchestrator
from rhetorica_backend.domain.chat.orchestration.state import (
    SqlExecutionAttempt,
    ToolOrchestrationState,
)


def _orchestrator() -> ChatOrchestrator:
    return ChatOrchestrator(
        llm_connector_repository=None,
        openmetadata_connector_repository=None,
        metadata_fetcher=None,
        sql_router=None,
        conversation_manager=None,
        db_connector_repository=None,
    )


def test_build_finalizer_context_includes_semantic_grounding() -> None:
    state = ToolOrchestrationState(
        semantic_grounding=SemanticGrounding(
            chosen_table_fqn="Sample-ClickHouse.analytics.dm.sales_orders",
            selection_rationale=(
                "Selected `Sample-ClickHouse.analytics.dm.sales_orders` because it has strong governance signals."
            ),
            candidates=[
                SemanticGroundingCandidate(
                    fqn="Sample-ClickHouse.analytics.dm.sales_orders",
                    service="Sample-ClickHouse",
                    database="analytics",
                    schema="dm",
                    relevance_score=99.0,
                    matched_on=["name_exact", "business_schema"],
                    reason_codes=["governance_strength", "business_layer_preference"],
                    certification="Certification.Gold",
                    tier="Tier.Tier1",
                    owners=["analytics"],
                    domains=["sales"],
                    usage_count=120,
                    suspicious=False,
                )
            ],
        )
    )

    context = shared_kernel.build_finalizer_context(
        raw_final_text="Нашлась таблица.",
        state=state,
    )

    assert context["semantic_grounding"]["chosen_table_fqn"] == (
        "Sample-ClickHouse.analytics.dm.sales_orders"
    )
    assert context["metadata_context"]["runtime_context"]["semantic_grounding"][
        "selection_rationale"
    ].startswith("Selected `Sample-ClickHouse.analytics.dm.sales_orders`")


def test_synthesize_evidence_enriches_metadata_snippet_with_governance_signals() -> None:
    state = ToolOrchestrationState(
        context_tables=["Sample-ClickHouse.analytics.dm.sales_orders"],
        metadata_sources={"mcp"},
        semantic_grounding=SemanticGrounding(
            chosen_table_fqn="Sample-ClickHouse.analytics.dm.sales_orders",
            candidates=[
                SemanticGroundingCandidate(
                    fqn="Sample-ClickHouse.analytics.dm.sales_orders",
                    service="Sample-ClickHouse",
                    database="analytics",
                    schema="dm",
                    relevance_score=99.0,
                    matched_on=["name_exact", "business_schema"],
                    reason_codes=["governance_strength", "business_layer_preference"],
                    certification="Certification.Gold",
                    tier="Tier.Tier1",
                    owners=["analytics"],
                    domains=["sales"],
                    usage_count=120,
                    suspicious=False,
                )
            ],
            selection_rationale="Selected `Sample-ClickHouse.analytics.dm.sales_orders` because it has strong governance signals.",
        ),
    )

    evidence = shared_kernel.synthesize_evidence(state=state)

    assert evidence[0]["type"] == "metadata_entity"
    assert "certification=Certification.Gold" in str(evidence[0]["snippet"])
    assert "matched_on=name_exact, business_schema" in str(evidence[0]["snippet"])


def test_build_final_payload_does_not_rewrite_answer_for_governed_choice() -> None:
    orchestrator = _orchestrator()
    state = ToolOrchestrationState(
        semantic_grounding=SemanticGrounding(
            chosen_table_fqn="Sample-ClickHouse.analytics.dm.sales_orders",
            selection_rationale="Selected the governed business-layer source.",
            candidates=[
                SemanticGroundingCandidate(
                    fqn="Sample-ClickHouse.analytics.dm.sales_orders",
                    service="Sample-ClickHouse",
                    database="analytics",
                    schema="dm",
                    relevance_score=99.0,
                    matched_on=["name_exact", "business_schema"],
                    reason_codes=[
                        "governance_strength",
                        "business_layer_preference",
                        "preferred_connector_map_match",
                    ],
                    certification="Certification.Gold",
                    tier="Tier.Tier1",
                    owners=["analytics"],
                    domains=["sales"],
                    usage_count=120,
                    suspicious=False,
                ),
                SemanticGroundingCandidate(
                    fqn="Sample-ClickHouse.analytics.imp_prod.sales_orders",
                    service="Sample-ClickHouse",
                    database="analytics",
                    schema="imp_prod",
                    relevance_score=88.0,
                    matched_on=["name_exact"],
                    reason_codes=["governance_strength"],
                    certification=None,
                    tier=None,
                    owners=["analytics"],
                    domains=["sales"],
                    usage_count=3,
                    suspicious=False,
                ),
            ],
        )
    )

    payload = orchestrator._build_final_payload_from_parsed(
        parsed_payload={
            "answer": "Нашлась основная витрина заказов.",
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [],
        },
        fallback_answer=None,
        state=state,
    )

    assert payload["answer"] == "Нашлась основная витрина заказов."


def test_build_final_payload_does_not_append_weak_match_note() -> None:
    orchestrator = _orchestrator()
    state = ToolOrchestrationState(
        semantic_grounding=SemanticGrounding(
            chosen_table_fqn="Sample-ClickHouse.default.dm.order",
            selection_rationale=(
                "Selected `Sample-ClickHouse.default.dm.order` because best available match is weak."
            ),
            candidates=[
                SemanticGroundingCandidate(
                    fqn="Sample-ClickHouse.default.dm.order",
                    service="Sample-ClickHouse",
                    database="default",
                    schema="dm",
                    relevance_score=0.0,
                    matched_on=[],
                    reason_codes=["governance_strength"],
                    certification=None,
                    tier=None,
                    owners=["Магнит Плюс"],
                    domains=[],
                    usage_count=0,
                    suspicious=False,
                ),
                SemanticGroundingCandidate(
                    fqn="Sample-ClickHouse.default.ft_sandbox.order",
                    service="Sample-ClickHouse",
                    database="default",
                    schema="ft_sandbox",
                    relevance_score=-10.0,
                    matched_on=["name_exact"],
                    reason_codes=["suspicious_scope_penalty"],
                    certification=None,
                    tier=None,
                    owners=[],
                    domains=[],
                    usage_count=0,
                    suspicious=True,
                ),
            ],
        )
    )

    payload = orchestrator._build_final_payload_from_parsed(
        parsed_payload={
            "answer": "Посчитал количество заказов по дням.",
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [],
        },
        fallback_answer=None,
        state=state,
    )

    assert payload["answer"] == "Посчитал количество заказов по дням."


def test_deterministic_finalizer_fallback_preserves_raw_sql_answer() -> None:
    orchestrator = _orchestrator()
    execution = ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT 1",
    )
    state = ToolOrchestrationState(
        execution=execution,
        generated_sql="SELECT 1",
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT 1",
                connector_name="clickhouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
        semantic_grounding=SemanticGrounding(
            chosen_table_fqn="Sample-ClickHouse.default.dm.order",
            selection_rationale=(
                "Selected `Sample-ClickHouse.default.dm.order` because best available match is weak."
            ),
            candidates=[
                SemanticGroundingCandidate(
                    fqn="Sample-ClickHouse.default.dm.order",
                    service="Sample-ClickHouse",
                    database="default",
                    schema="dm",
                    relevance_score=0.0,
                    matched_on=[],
                    reason_codes=[],
                    certification=None,
                    tier=None,
                    owners=[],
                    domains=[],
                    usage_count=0,
                    suspicious=False,
                )
            ],
        ),
    )

    payload = orchestrator._deterministic_finalizer_fallback(
        state=state,
        raw_final_text="| Дата | Заказы |\n|---|---|\n| 2026-04-01 | 10 |",
    )

    assert payload["answer"] == "| Дата | Заказы |\n|---|---|\n| 2026-04-01 | 10 |"
    assert payload["execution"]["execution_id"] == "exec-1"
    assert payload["sql"] == "SELECT 1"


def test_deterministic_finalizer_fallback_preserves_raw_contract_answer() -> None:
    orchestrator = _orchestrator()
    execution = ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT 1",
    )
    state = ToolOrchestrationState(
        execution=execution,
        generated_sql="SELECT 1",
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT 1",
                connector_name="clickhouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
    )
    raw_payload = {
        "schema_version": "v1",
        "answer_markdown": "| Дата | Заказы |\n|---|---|\n| 2026-05-01 | 10 |",
        "sql": "SELECT 1",
        "target": None,
        "claims": [],
        "evidence": [],
        "claim_evidence_links": [],
        "uncertainty_flags": [],
        "clarification_request": None,
        "analysis_document": None,
        "visualization": None,
    }

    payload = orchestrator._deterministic_finalizer_fallback(
        state=state,
        raw_final_text=json.dumps(raw_payload, ensure_ascii=False),
    )

    assert payload["answer"] == raw_payload["answer_markdown"]
    assert payload["execution"]["execution_id"] == "exec-1"
    assert payload["sql"] == "SELECT 1"


def test_postprocess_llm_payload_uses_selected_connector_dialect(monkeypatch) -> None:
    orchestrator = _orchestrator()
    now = datetime.now(tz=UTC)
    trino_connector = ConnectorItem(
        id="conn-trino",
        name="trino-adhoc",
        workspace_id="default",
        connection=EngineConnectionConfig(
            engine="trino",
            host="trino.local",
            port=443,
            username="analyst",
            catalog="iceberg",
            schema="iceberg",
        ),
        has_password=True,
        created_at=now,
        updated_at=now,
    )
    orchestrator._sql_router = SimpleNamespace(
        select_connector=lambda **_kwargs: trino_connector,
    )

    seen_dialects: list[str] = []

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.transport_payload_postprocessing.SqlGuardService.guard",
        lambda _self, request: seen_dialects.append(request.dialect)
        or SimpleNamespace(allowed=True, violations=[]),
    )

    payload = orchestrator._postprocess_llm_payload(
        request=SimpleNamespace(
            conversation_id=None,
            database_connector_name="trino-adhoc",
        ),
        llm_payload={"sql": "SELECT * FROM UNNEST(ARRAY[1, 2]) AS t(x)"},
        db_connectors=[trino_connector],
        warnings=[],
    )

    assert payload["sql"] == "SELECT * FROM UNNEST(ARRAY[1, 2]) AS t(x)"
    assert seen_dialects == ["trino"]
