import json

import pytest
from rhetorica_backend.domain.chat.llm.token_budget import LlmPromptBudgetService
from rhetorica_backend.domain.chat.orchestration.context_projection import (
    ContextProjection,
    ModelCallManifest,
)
from rhetorica_backend.domain.chat.orchestration.context_window import ContextWindow
from rhetorica_backend.domain.chat.orchestration.provider_usage import normalize_cache_usage
from rhetorica_backend.domain.chat.orchestration.tool_result_artifacts import (
    ToolResultArtifactStore,
)
from rhetorica_backend.domain.chat.orchestration.tool_result_prompt_serializer import (
    ToolResultPromptSerializer,
)


@pytest.fixture(autouse=True)
def enable_output_policy(monkeypatch):
    from rhetorica_backend.domain.chat import settings

    monkeypatch.setattr(
        settings, "_settings", settings.ChatSettings(tool_output_policy_enabled=True)
    )


def test_input_reserve_is_counted_once():
    budget = LlmPromptBudgetService(
        model_name="deepseek", context_window_tokens=180000, response_reserve_tokens=32000
    )
    assert budget.prompt_budget_tokens() * 0.8 == 118400
    assert budget.estimate_request(messages=[]).tokens == 3


def test_large_mcp_catalog_keeps_exact_names_and_advances_native_pages():
    tools = [
        {
            "remote_tool_name": f"remote_{i}",
            "exposed_tool_name": f"mcp_exact_{i}",
            "description": '"\\' * 3000,
        }
        for i in range(143)
    ]
    serializer = ToolResultPromptSerializer()
    seen = []
    offset = 0
    while offset < len(tools):
        raw = {
            "ok": True,
            "connector_name": "exact_connector",
            "offset": offset,
            "limit": 50,
            "total": len(tools),
            "tools": tools[offset : offset + 50],
            "tool_artifact": {"artifact_id": "catalog-" + str(offset)},
        }
        output = serializer.serialize(tool_name="list_mcp_tools", tool_result=raw)
        assert len(output) <= 16_000
        payload = json.loads(output)["compact_payload"]
        assert payload["connector_name"] == "exact_connector"
        assert payload["offset"] == offset
        assert payload["tools"]
        seen.extend(item["remote_tool_name"] for item in payload["tools"])
        offset += len(payload["tools"])
        assert payload["next_offset"] == (offset if offset < len(tools) else None)
    assert seen == [item["remote_tool_name"] for item in tools]


def test_large_group_catalog_has_readable_exact_continuation():
    store = ToolResultArtifactStore()
    groups = [
        {
            "group_name": f"group_{i}",
            "description": "desc" * 400,
            "tool_names": [f"tool_{i}"],
            "tool_count": 1,
        }
        for i in range(100)
    ]
    raw = {"ok": True, "groups": groups, "group_count": len(groups)}
    handle = store.register(tool_name="list_tool_groups", arguments={}, result=raw)
    output = ToolResultPromptSerializer().serialize(
        tool_name="list_tool_groups", tool_result={**raw, "tool_artifact": handle}
    )
    assert len(output) <= 16_000
    payload = json.loads(output)["compact_payload"]
    count = len(payload["groups"])
    assert 0 < count < len(groups)
    assert payload["catalog_page"]["next_offset"] == count
    assert [g["group_name"] for g in payload["groups"]] == [g["group_name"] for g in groups[:count]]
    rest = store.read(
        artifact_id=handle["artifact_id"],
        format="json",
        json_pointer="/groups",
        offset=count,
        limit=2,
        fields=["group_name"],
    )
    assert rest["data"] == [{"group_name": f"group_{i}"} for i in range(count, count + 2)]


def test_single_oversized_catalog_identifier_has_no_nonadvancing_offset():
    raw = {
        "ok": True,
        "connector_name": "exact",
        "offset": 0,
        "total": 1,
        "tools": [
            {"remote_tool_name": "x" * 20_000, "exposed_tool_name": "exact", "description": ""}
        ],
        "tool_artifact": {"artifact_id": "exact-artifact"},
    }
    output = ToolResultPromptSerializer().serialize(tool_name="list_mcp_tools", tool_result=raw)
    assert len(output) <= 16_000
    payload = json.loads(output)["compact_payload"]
    assert payload["tool_artifact"]["artifact_id"] == "exact-artifact"
    assert payload["json_pointer"] == "/tools"
    assert "next_offset" not in payload


@pytest.mark.parametrize(
    "raw,read,reported",
    [
        (None, 0, False),
        ({"input_tokens": 50}, 0, False),
        ({"input_tokens_details": {"cached_tokens": 0}}, 0, True),
        ({"prompt_cache_hit_tokens": 0}, 0, True),
        ({"prompt_cache_hit_tokens": 70}, 70, True),
        ({"cache_read_input_tokens": 42}, 42, True),
        ({"cache_creation_input_tokens": 42}, 0, False),
    ],
)
def test_provider_cache_zero_is_not_missing(raw, read, reported):
    usage = normalize_cache_usage(raw)
    assert (usage.read_tokens, usage.reported) == (read, reported)


def test_projection_replays_exact_prefix_and_rejects_stale_anchor():
    projection = ContextProjection()
    first = [{"role": "user", "content": "question"}]
    event = {"role": "user", "content": "fixed service event"}
    projection.append(first, event)
    sent = projection.project(first)
    later = [*first, {"role": "assistant", "content": "answer"}]
    assert projection.project(later)[: len(sent)] == sent
    assert projection.project([{"role": "user", "content": "replacement"}]) == [
        {"role": "user", "content": "replacement"}
    ]
    restored = ContextProjection(events=json.loads(json.dumps(projection.events)))
    assert restored.project(later) == projection.project(later)


def test_giant_openmetadata_result_is_bounded_retrievable_and_deduplicated():
    store = ToolResultArtifactStore()
    entities = {
        "data": [
            {"id": str(i), "fullyQualifiedName": f"db.schema.metric_{i}", "description": "x" * 5000}
            for i in range(1500)
        ]
    }
    result = {
        "ok": True,
        "remote_tool_name": "search",
        "connector_name": "openmetadata",
        "result": {
            "structuredContent": entities,
            "content": [
                {"type": "text", "text": json.dumps(entities)},
                {"type": "text", "text": "Independent warning: incomplete search index."},
            ],
        },
    }
    handle = store.register(tool_name="mcp_openmetadata_search", arguments={}, result=result)
    output = ToolResultPromptSerializer().serialize(
        tool_name="mcp_openmetadata_search", tool_result={**result, "tool_artifact": handle}
    )
    assert len(output) <= 12000
    assert handle["artifact_id"] in output
    assert "Independent warning" in output
    # Exact raw result remains available, even above the durable retention limit.
    page = store.read(
        artifact_id=handle["artifact_id"],
        json_pointer="/result/structuredContent/data",
        format="json",
        fields=["id", "fullyQualifiedName"],
        limit=2,
    )
    assert page["data"] == [
        {"id": "0", "fullyQualifiedName": "db.schema.metric_0"},
        {"id": "1", "fullyQualifiedName": "db.schema.metric_1"},
    ]
    assert page["next_offset"] == 2
    parsed = store.read(
        artifact_id=handle["artifact_id"],
        json_pointer="/result/content/0/text",
        format="json",
        parse_json=True,
        fields=["missing"],
    )
    assert parsed["data"] == {}


def test_json_reader_never_splits_an_oversized_item():
    store = ToolResultArtifactStore()
    handle = store.register(
        tool_name="test", arguments={}, result={"items": [{"id": "1", "large": "x" * 20000}]}
    )
    page = store.read(artifact_id=handle["artifact_id"], json_pointer="/items", format="json")
    assert page["error_code"] == "artifact_item_exceeds_page_budget"
    assert page["next_offset"] is None
    page = store.read(
        artifact_id=handle["artifact_id"], json_pointer="/items", format="json", fields=["id"]
    )
    assert page["data"] == [{"id": "1"}]


def test_context_window_keeps_last_three_batches_and_pending_calls():
    items = [{"role": "user", "content": "current request"}]
    for i in range(5):
        items += [
            {"type": "function_call", "call_id": str(i), "arguments": "{}"},
            {
                "type": "function_call_output",
                "call_id": str(i),
                "output": json.dumps({
                    "compact_payload": {
                        "tool_artifact": {"artifact_id": "ta_1"},
                        "data": "x" * 3000,
                    }
                }),
            },
        ]
    items.append({"type": "function_call", "call_id": "pending", "arguments": "{}"})
    trimmed, count = ContextWindow().trim(items)
    assert count == 2
    assert trimmed[5:] == items[5:]
    prefix, tail = ContextWindow().split(items)
    assert items[0] not in prefix
    assert tail == [items[0], *items[5:]]


def test_manifest_detects_tool_schema_changes_without_content():
    manifest = ModelCallManifest()
    args = dict(  # noqa: C408 - named fixture fields
        instructions="private instructions",
        items=[{"role": "user", "content": "private question"}],
        estimated_tokens=20,
        budget_tokens=100,
        reason=None,
    )
    manifest.build(**args, tools=[])
    changed = manifest.build(**args, tools=[{"name": "new"}], instructions_version="prompt-v2")
    assert changed["prefix_change"] == "tool_schemas_changed"
    assert changed["instructions_version"] == "prompt-v2"
    assert "private" not in json.dumps(changed)


def test_compaction_without_tools_keeps_the_upcoming_budget_notice():
    old = {"role": "assistant", "content": "old answer"}
    question = {"role": "user", "content": "current question"}
    notice = {"role": "user", "content": "<runtime_context>Used 50/100</runtime_context>"}
    prefix, tail = ContextWindow().split([old, question, notice])
    assert prefix == [old]
    assert tail == [question, notice]


def test_protected_batch_keeps_reasoning_separated_from_calls_by_assistant_text():
    items = [{"role": "user", "content": "task"}]
    for i in range(5):
        items += [
            {"type": "reasoning", "id": f"r-{i}", "summary": []},
            {"role": "assistant", "type": "message", "content": f"step {i}"},
            {"type": "function_call", "call_id": str(i)},
            {"type": "function_call_output", "call_id": str(i), "output": "ok"},
        ]
    _, tail = ContextWindow().split(items)
    assert tail == [items[0], *items[9:]]


def test_search_preview_keeps_candidate_ids_when_descriptions_are_huge():
    candidates = [
        {
            "entityType": "metric",
            "fullyQualifiedName": f"orders_{i}",
            "name": f"Orders {i}",
            "description": "long description " * 4000,
            "columns": [{"name": f"field_{j}"} for j in range(200)],
        }
        for i in range(10)
    ]
    result = {
        "ok": True,
        "remote_tool_name": "search_metadata",
        "result": {"structuredContent": {"results": candidates, "totalFound": 100}, "content": []},
        "tool_artifact": {"artifact_id": "ta_" + "a" * 32},
    }
    output = ToolResultPromptSerializer().serialize(
        tool_name="openmetadata__search_metadata", tool_result=result
    )
    assert len(output) <= 12_000
    assert "orders_0" in output
    assert "entityType" in output
    assert "ta_" + "a" * 32 in output


def test_reader_pages_escaped_text_without_skipping_characters():
    store = ToolResultArtifactStore()
    data = '\\"\n' * 10000
    handle = store.register(tool_name="test", arguments={}, result={"text": data})
    offset = 0
    chunks = []
    while True:
        page = store.read(
            artifact_id=handle["artifact_id"],
            json_pointer="/text",
            format="json",
            offset=offset,
            limit=8000,
        )
        assert len(json.dumps(page, ensure_ascii=False)) < 12000
        chunks.append(page["data"])
        if not page["has_more"]:
            break
        offset = page["next_offset"]
    assert "".join(chunks) == data


def test_completed_duplicate_call_ids_and_pending_batch_are_not_split():
    items = [{"role": "user", "content": "task"}]
    for _ in range(5):
        items.extend([
            {"type": "function_call", "call_id": "reused"},
            {"type": "function_call_output", "call_id": "reused"},
        ])
    items += [
        {"type": "function_call", "call_id": "one"},
        {"type": "function_call", "call_id": "two"},
        {"type": "function_call_output", "call_id": "one"},
    ]
    _, tail = ContextWindow().split(items)
    assert tail[-3:] == items[-3:]
    assert tail[1:] == items[5:]


def test_gate_rejects_incomplete_and_unrated_measurements():
    from rhetorica_backend.domain.chat.eval.context_harness import (
        HarnessMeasurement,
        evaluate_harness,
    )

    row = HarnessMeasurement(
        case_id="one",
        provider="deepseek",
        mode="direct",
        repeat=1,
        fixture_hash="",
        input_tokens=100,
        cached_input_tokens=90,
        cache_reported=True,
        latency_ms=1,
        completed=True,
    )
    result = evaluate_harness(
        [row],
        baseline=[row.model_copy(update={"mode": "baseline"})],
        case_ids={"one"},
        provider="deepseek",
    )
    assert result.passed is False
    assert "candidate_quality_unrated" in result.failures
    assert "external_results_not_comparable" in result.failures
    assert "candidate_matrix_incomplete_or_duplicated" in result.failures


def test_certified_reads_overlap_but_writes_wait_for_all_readers():
    import asyncio

    from rhetorica_backend.domain.chat.orchestration.tool_execution_gate import ToolExecutionGate

    async def run():
        gate = ToolExecutionGate(enabled=True)
        ready = asyncio.Event()
        release = asyncio.Event()
        active = 0
        maximum = 0
        events = []

        async def read():
            nonlocal active, maximum
            async with gate.enter("read_tool_artifact"):
                active += 1
                maximum = max(maximum, active)
                if active == 4:
                    ready.set()
                await release.wait()
                active -= 1
                events.append("read")

        async def write():
            async with gate.enter("unknown_mcp_tool"):
                assert active == 0
                events.append("write")

        readers = [asyncio.create_task(read()) for _ in range(4)]
        await ready.wait()
        writer = asyncio.create_task(write())
        await asyncio.sleep(0)
        assert not writer.done()
        release.set()
        await asyncio.gather(*readers, writer)
        assert maximum == 4
        assert events[-1] == "write"

    asyncio.run(run())


def test_selected_skill_fragment_is_bounded_and_protected():
    content = "Full skill instructions\n" * 2000
    output = ToolResultPromptSerializer().serialize(
        tool_name="read_workspace_skill",
        tool_result={
            "ok": True,
            "content": content,
            "skill_id": "skill",
            "tool_artifact": {"artifact_id": "ta_skill"},
        },
    )
    compact_payload = json.loads(output)["compact_payload"]
    assert "content" not in compact_payload
    assert compact_payload["tool_artifact"] == {"artifact_id": "ta_skill"}
    items = [
        {"role": "user", "content": "current"},
        {"type": "function_call", "call_id": "skill", "name": "read_workspace_skill"},
        {"type": "function_call_output", "call_id": "skill", "output": output},
    ]
    for i in range(4):
        items.extend([
            {"type": "function_call", "call_id": str(i)},
            {"type": "function_call_output", "call_id": str(i), "output": "ok"},
        ])
    window = ContextWindow(protected_tool_call_ids=frozenset({"skill"}))
    trimmed, count = window.trim(items)
    assert trimmed[2] == items[2]
    prefix, tail = window.split(items)
    assert items[1] not in prefix and items[2] not in prefix
    assert tail[:3] == items[:3]


def test_anthropic_inclusive_usage_does_not_double_count_openai():
    from rhetorica_backend.domain.chat.orchestration.provider_usage import inclusive_input_tokens

    raw = {"input_tokens": 30, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 20}
    assert inclusive_input_tokens(raw, sdk_input=30, usage_format="anthropic") == 100
    assert inclusive_input_tokens(raw, sdk_input=100, usage_format="openai") == 100


def test_discovery_is_order_independent_ranked_and_paged():
    from rhetorica_backend.domain.chat.orchestration.tool_discovery import ToolDiscoveryPolicy

    definitions = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name, description in [
            ("list_mcp_tools", "List connector tools"),
            ("load_tool_group", "Load an MCP group"),
            ("read_metric", "Read a metric"),
        ]
    ]
    policy = ToolDiscoveryPolicy()
    first = policy.search(definitions, query="mcp tools", limit=1)
    second = policy.search(list(reversed(definitions)), query="mcp tools", limit=1)
    assert first["tools"] == second["tools"]
    assert first["tools"][0]["tool_id"] == "list_mcp_tools"
    assert first["next_offset"] == 1
    page = policy.search(definitions, query="mcp tools", offset=1)
    assert page["tools"][0]["tool_id"] == "load_tool_group"
    assert page["next_offset"] is None
    assert policy.search(definitions, query="read_metric")["tools"][0]["tool_id"] == "read_metric"


def test_provider_capabilities_are_explicit_and_do_not_infer_hosted_features():
    from rhetorica_backend.domain.chat.orchestration.provider_capabilities import (
        ProviderRuntimeCapabilities,
    )

    default = ProviderRuntimeCapabilities.from_settings({})
    assert default.requires_last_user
    assert not default.parallel_tool_calls
    profile = ProviderRuntimeCapabilities.from_settings({
        "runtime_context_role": "developer",
        "runtime_requires_last_user": True,
        "runtime_reasoning_replay": True,
        "runtime_usage_format": "deepseek",
    })
    assert profile.runtime_role == "developer" and profile.requires_last_user
    assert profile.reasoning_replay and profile.usage_format == "deepseek"


def test_gate_passes_only_complete_reviewed_comparable_matrix():
    from rhetorica_backend.domain.chat.eval.context_harness import (
        HarnessMeasurement,
        evaluate_harness,
    )

    cases = {f"case-{i}" for i in range(30)}
    baseline = [
        HarnessMeasurement(
            case_id=case,
            provider="deepseek",
            mode="baseline",
            repeat=repeat,
            fixture_hash="frozen-external-data",
            input_tokens=100,
            cached_input_tokens=70,
            cache_reported=True,
            latency_ms=100,
            completed=True,
            answer_correct=True,
            sql_correct=True,
            grounded=True,
        )
        for case in cases
        for repeat in (1, 2, 3)
    ]
    candidate = [
        row.model_copy(update={"mode": "direct", "input_tokens": 90, "latency_ms": 90})
        for row in baseline
    ]
    assert evaluate_harness(
        candidate, baseline=baseline, case_ids=cases, provider="deepseek"
    ).passed
    candidate[0] = candidate[0].model_copy(update={"grounded": False})
    assert (
        "grounded_regressed"
        in evaluate_harness(
            candidate, baseline=baseline, case_ids=cases, provider="deepseek"
        ).failures
    )


def test_stable_connector_catalog_ignores_repository_row_order():
    from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder

    def render(names, optimized):
        return PromptBuilder.build_user_message(
            question="question",
            db_connectors=[],
            mcp_connector_names=names,
            rag_connector_names=names,
            rag_enabled=True,
            connector_catalog_only=optimized,
        )

    assert render(["z", "a"], True) == render(["a", "z"], True)
    assert render(["z", "a"], False) != render(["a", "z"], False)


@pytest.mark.parametrize(
    "tool_name,payload",
    [
        ("read_documentation_page", {"page": {"content": "text" * 100000}}),
        ("read_gitlab_file", {"file": {"content": "code" * 100000}}),
        ("get_airflow_task_log", {"airflow": {"content": "log" * 100000}}),
        ("load_tool_group", {"loaded_tools": ["tool" * 50000] * 10}),
        ("unknown_future_tool", {"results": [{"description": "x" * 100000}] * 10}),
        ("run_sql", {"rows": [["value" * 100000]] * 100, "row_count": 100}),
    ],
)
def test_every_tool_family_has_a_hard_envelope_limit_and_exact_artifact(tool_name, payload):
    store = ToolResultArtifactStore()
    result = {"ok": True, **payload}
    handle = store.register(tool_name=tool_name, arguments={}, result=result)
    output = ToolResultPromptSerializer().serialize(
        tool_name=tool_name, tool_result={**result, "tool_artifact": handle}
    )
    assert len(output) <= 16000
    assert handle["artifact_id"] in output
    page = store.read(artifact_id=handle["artifact_id"], format="json", fields=["ok"])
    assert page["data"] == {"ok": True}


@pytest.mark.parametrize("remote_name", ["openmetadata", "gitlab", "another_mcp"])
def test_mcp_limit_is_provider_independent(remote_name):
    raw = {
        "ok": True,
        "remote_tool_name": remote_name,
        "result": {"structuredContent": {"data": '\\"' * 500000}},
        "tool_artifact": {"artifact_id": "ta_exact", "durable": False},
    }
    output = ToolResultPromptSerializer().serialize(tool_name="mcp_" + remote_name, tool_result=raw)
    assert len(output) <= 12000
    assert "ta_exact" in output


def test_concurrent_certified_reads_retain_all_audit_entries():
    from concurrent.futures import ThreadPoolExecutor

    from rhetorica_backend.domain.chat.orchestration.capability_registry import (
        RuntimeCapabilityState,
        register_tool_usage,
    )

    state = RuntimeCapabilityState()

    def record(index):
        register_tool_usage(
            runtime_state=state, tool_name=f"tool_{index}", providers=[f"provider_{index}"]
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(record, range(200)))
    assert set(state.used_tools) == {f"tool_{i}" for i in range(200)}
    assert set(state.used_providers) == {f"provider_{i}" for i in range(200)}
