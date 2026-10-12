"""Bounded evidence pages must show identities without hiding their incompleteness."""

import json

import pytest
from rhetorica_backend.domain.chat.orchestration.prompt_artifact_serializer import (
    PromptArtifactSerializer,
    PromptContextBudgetPolicy,
)
from rhetorica_backend.domain.chat.orchestration.record_preview import RecordPreviewPolicy
from rhetorica_backend.domain.chat.orchestration.tool_result_artifacts import (
    ToolResultArtifactStore,
)
from rhetorica_backend.domain.chat.orchestration.tool_result_prompt_serializer import (
    ToolResultPromptSerializer,
)


def records():
    return [
        {
            "id": f"opaque-{i}", "name": f"metric_{i}", "displayName": f"Метрика {i}",
            "description": 'Text with "quotes" and \\ escapes. ' * 2000,
            "owners": [{"id": "owner-id", "name": "analytics", "description": "owner" * 1000}],
            "tags": [{"tagFQN": "domain.metric", "description": "tag" * 1000}],
            "metricExpression": {"code": f"SELECT {i}"}, "deleted": i == 2,
        }
        for i in range(9)
    ]


@pytest.mark.parametrize("remote_name,id_field", [
    ("metadata_search", "id"), ("gitlab_search", "path"), ("custom_search", "key"),
])
@pytest.mark.parametrize("list_key", ["results", "data", "issues", "nodes", None])
def test_nine_large_records_keep_all_exact_ids_and_readable_full_evidence(
    monkeypatch, remote_name, id_field, list_key,
):
    from rhetorica_backend.domain.chat import settings

    monkeypatch.setattr(settings, "_settings", settings.ChatSettings(tool_output_policy_enabled=True))
    source = records()
    if id_field != "id":
        source = [{id_field: row["id"], **{k: v for k, v in row.items() if k != "id"}}
                  for row in source]
    structured = {list_key: source} if list_key else source
    raw = {"ok": True, "remote_tool_name": remote_name,
           "result": {"structuredContent": structured}}
    store = ToolResultArtifactStore()
    handle = store.register(tool_name=remote_name, arguments={}, result=raw)
    output = ToolResultPromptSerializer().serialize(
        tool_name=remote_name, tool_result={**raw, "tool_artifact": handle},
    )
    assert len(output) <= 12000
    envelope = json.loads(output)
    content = envelope["compact_payload"]["mcp"]["structured_content"]
    page = content[list_key] if list_key else content
    assert [row[id_field] for row in page["items"]] == [row[id_field] for row in source]
    assert page["source_count"] == page["shown_count"] == 9
    assert page["items_omitted"] == 0 and page["next_offset"] is None
    assert page["details_omitted"] and envelope["truncated"]
    assert page["items"][2]["deleted"] is True
    exact = store.read(
        artifact_id=handle["artifact_id"], format="json",
        json_pointer="/result/structuredContent" + ("/" + list_key if list_key else ""),
        offset=8, limit=1,
        fields=[id_field, "metricExpression"],
    )
    assert exact["data"] == [{id_field: "opaque-8", "metricExpression": {"code": "SELECT 8"}}]
    assert source[0]["description"].endswith('Text with "quotes" and \\ escapes. ')


def test_oversized_escaped_identifier_is_omitted_whole_with_no_stuck_cursor():
    page = RecordPreviewPolicy().project([{"id": '\\"' * 10000}])
    assert page["items"] == []
    assert page["source_count"] == page["items_omitted"] == 1
    assert page["shown_count"] == 0 and page["next_offset"] is None
    assert len(json.dumps(page)) <= 5000


def test_page_limits_preserve_order_and_recompute_counts():
    source = [{"id": str(i)} for i in range(80)]
    page = RecordPreviewPolicy(max_items=20).project(source)
    assert page["next_offset"] == 20
    reduced = RecordPreviewPolicy.shrink(page, 3)
    assert reduced["items"] == source[:3]
    assert reduced["source_count"] == 80 and reduced["shown_count"] == 3
    assert reduced["items_omitted"] == 77 and reduced["next_offset"] == 3


@pytest.mark.parametrize("source", [[], [1, 2], [{"value": 1}], [{"id": []}]])
def test_unsupported_shapes_keep_existing_serializer_contract(source):
    assert RecordPreviewPolicy().project(source) is None


def test_small_custom_records_do_not_gain_an_expensive_projection_wrapper():
    source = [{"id": "a", "arbitrary_value": 42}]
    assert RecordPreviewPolicy().project(source) is None
    envelope = PromptArtifactSerializer(optimized_outputs=True).serialize(
        artifact_type="evidence_summary", artifact={
            "remote_tool_name": "custom", "result": {"structuredContent": {"data": source}},
        },
    )
    assert envelope.compact_payload["mcp"]["structured_content"]["data"] == source


def test_legacy_outputs_and_sql_rows_keep_their_existing_shape():
    source = records()
    legacy = PromptArtifactSerializer(optimized_outputs=False).serialize(
        artifact_type="evidence_summary", artifact={"results": source},
    )
    assert "preview_kind" not in legacy.stable_json()
    optimized = PromptArtifactSerializer(optimized_outputs=True).serialize(
        artifact_type="evidence_summary", artifact={"rows": [{"id": "a", "value": 42}]},
    )
    assert optimized.compact_payload["rows"] == [{"id": "a", "value": 42}]


def test_second_stage_fitting_never_shortens_projected_identifiers():
    source = [{"id": "id-" + str(i) + "x" * 300} for i in range(9)]
    envelope = PromptArtifactSerializer(
        optimized_outputs=True,
        budget_policy=PromptContextBudgetPolicy(max_payload_chars=2200),
    ).serialize(
        artifact_type="evidence_summary",
        artifact={"results": source, "summary": "s" * 500, "description": "d" * 500},
    )
    page = envelope.compact_payload["results"]
    assert page["items"] == source[:page["shown_count"]]
    assert page["shown_count"] + page["items_omitted"] == 9


def test_json_in_mcp_text_gets_the_same_page_and_preserves_independent_text():
    envelope = PromptArtifactSerializer(optimized_outputs=True).serialize(
        artifact_type="evidence_summary", artifact={
            "remote_tool_name": "custom_text_search", "result": {"content": [
                {"type": "text", "text": json.dumps({"issues": records()})},
                {"type": "text", "text": "Search covers the current project only."},
            ]},
        },
    )
    mcp = envelope.compact_payload["mcp"]
    assert mcp["content_json"][0]["issues"]["shown_count"] == 9
    assert mcp["content_markdown"] == "Search covers the current project only."


def test_external_preview_marker_cannot_impersonate_internal_page():
    envelope = PromptArtifactSerializer(
        optimized_outputs=True, budget_policy=PromptContextBudgetPolicy(max_payload_chars=1000),
    ).serialize(
        artifact_type="evidence_summary",
        artifact={"entity": {"preview_kind": "record_page", "description": "x" * 9000},
                  "summary": "s" * 500},
    )
    assert len(envelope.stable_json()) < 16000
