"""Static DAG validation and deterministic control-flow decisions."""

from __future__ import annotations

import operator
from collections import deque
from dataclasses import dataclass
from typing import Any

from rhetorica_backend.contracts.scenario_workflows import (
    ConditionNode,
    InputBinding,
    ScenarioEdge,
    ScenarioNode,
    ScenarioWorkflowDefinition,
    ValueSpec,
    ValueType,
    WorkflowValidationIssue,
    WorkflowValidationResult,
)
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.values import check_schema, validate_value

BranchPath = frozenset[tuple[str, str]]


@dataclass(frozen=True)
class GraphIndex:
    nodes: dict[str, ScenarioNode]
    incoming: dict[str, list[ScenarioEdge]]
    outgoing: dict[str, list[ScenarioEdge]]
    order: list[str]


def index_graph(definition: ScenarioWorkflowDefinition) -> GraphIndex:
    nodes = {node.id: node for node in definition.nodes}
    incoming: dict[str, list[ScenarioEdge]] = {key: [] for key in nodes}
    outgoing: dict[str, list[ScenarioEdge]] = {key: [] for key in nodes}
    for edge in definition.edges:
        if edge.source in nodes and edge.target in nodes:
            incoming[edge.target].append(edge)
            outgoing[edge.source].append(edge)
    degree = {key: len(edges) for key, edges in incoming.items()}
    queue = deque(key for key in nodes if degree[key] == 0)
    order: list[str] = []
    while queue:
        key = queue.popleft()
        order.append(key)
        for edge in outgoing[key]:
            degree[edge.target] -= 1
            if degree[edge.target] == 0:
                queue.append(edge.target)
    return GraphIndex(nodes, incoming, outgoing, order)


def _simplify_paths(paths: set[BranchPath]) -> set[BranchPath]:
    """Reduce branch predicates, including true/false pairs at a merge."""
    while True:
        reduced = {path for path in paths if not any(other < path for other in paths)}
        added: set[BranchPath] = set()
        ordered = sorted(reduced, key=lambda value: sorted(value))
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                delta = left ^ right
                if len(delta) == 2 and len({key for key, _ in delta}) == 1:
                    added.add(left & right)
        if not added - reduced:
            return reduced
        paths = reduced | added


def activation_paths(graph: GraphIndex) -> dict[str, set[BranchPath]]:
    result: dict[str, set[BranchPath]] = {}
    for key in graph.order:
        paths: set[BranchPath] = set()
        if not graph.incoming[key]:
            paths.add(frozenset())
        for edge in graph.incoming[key]:
            for path in result[edge.source]:
                if edge.branch:
                    path = path | {(edge.source, edge.branch)}
                paths.add(path)
        if len(paths) > 512:
            raise WorkflowError("graph_branch_complexity_limit")
        result[key] = _simplify_paths(paths)
    return result


def _binding_spec(
    binding: InputBinding, definition: ScenarioWorkflowDefinition, graph: GraphIndex
) -> ValueSpec | None:
    if not binding.path or not isinstance(binding.path[0], str):
        return None
    if binding.source == "input":
        ports = definition.parameters
    else:
        source = graph.nodes.get(binding.node_id or "")
        if source is None:
            return None
        ports = definition.parameters if source.type == "input" else source.outputs
    spec = ports.get(binding.path[0])
    if spec is None:
        return None
    if spec.type == "files" and len(binding.path) == 2 and isinstance(binding.path[1], int):
        return ValueSpec(type="file", required=False)
    schema = spec.json_schema
    for part in binding.path[1:]:
        if schema is None:
            return ValueSpec(type="json")
        schema = (
            schema.get("properties", {}).get(part) if isinstance(part, str) else schema.get("items")
        )
    if len(binding.path) > 1:
        raw_type = (schema or {}).get("type")
        mapped: dict[str, ValueType] = {
            "string": "string",
            "integer": "number",
            "number": "number",
            "boolean": "boolean",
        }
        return ValueSpec(type=mapped.get(str(raw_type), "json"), json_schema=schema)
    return spec


def validate_workflow(definition: ScenarioWorkflowDefinition) -> WorkflowValidationResult:
    issues: list[WorkflowValidationIssue] = []

    def issue(code: str, message: str, node: str | None = None, field: str | None = None) -> None:
        issues.append(
            WorkflowValidationIssue(code=code, message=message, node_id=node, field=field)
        )

    graph = index_graph(definition)
    if len(graph.nodes) != len(definition.nodes):
        issue("duplicate_node_id", "Идентификаторы шагов должны быть уникальны.")
    starts = [node.id for node in definition.nodes if node.type == "input"]
    ends = [node.id for node in definition.nodes if node.type == "result"]
    if len(starts) != 1 or len(ends) != 1:
        issue("workflow_endpoints", "Нужны ровно один вход и один результат.")
    seen_edges: set[tuple[str, str, str | None]] = set()
    for edge in definition.edges:
        signature = (edge.source, edge.target, edge.branch)
        if signature in seen_edges:
            issue("duplicate_edge", "Связь уже существует.", edge.target)
        seen_edges.add(signature)
        if edge.source not in graph.nodes or edge.target not in graph.nodes:
            issue("unknown_edge_node", "Связь указывает на отсутствующий шаг.", edge.target)
        elif (graph.nodes[edge.source].type == "condition") != (edge.branch is not None):
            issue("invalid_branch", "Ветви true/false допустимы только у условия.", edge.source)
    if len(graph.order) != len(graph.nodes):
        issue("graph_cycle", "Циклы в этой версии сценариев не поддерживаются.")
        return WorkflowValidationResult(valid=False, issues=issues)
    ancestors: dict[str, set[str]] = {}
    for key in graph.order:
        ancestors[key] = set().union(
            *(ancestors[edge.source] | {edge.source} for edge in graph.incoming[key])
        )
    try:
        paths = activation_paths(graph)
    except WorkflowError as exc:
        issue(exc.code, str(exc))
        return WorkflowValidationResult(valid=False, issues=issues)
    for node in definition.nodes:
        key = node.id
        if node.type != "input" and (not starts or starts[0] not in ancestors[key]):
            issue("unreachable_node", "Шаг недостижим от входа.", key)
        if node.type != "result" and ends and key not in ancestors[ends[0]]:
            issue("no_result_path", "Из шага нет пути к результату.", key)
        if node.type == "input" and graph.incoming[key]:
            issue("input_has_predecessor", "Вход не может иметь предшествующий шаг.", key)
        if node.type == "result" and graph.outgoing[key]:
            issue("result_has_successor", "Результат завершает граф.", key)
        if node.type == "condition":
            if {edge.branch for edge in graph.outgoing[key]} != {"true", "false"}:
                issue("condition_branches", "Укажите обе ветви условия.", key)
            for rule in node.rules:
                if rule.input not in node.inputs:
                    issue("condition_input_missing", "Выберите вход условия.", key, rule.input)
        if node.type == "sql" and (not node.sql.strip() or not node.connector_name):
            issue("sql_configuration_required", "Укажите коннектор и SQL.", key)
        if node.type == "python" and not node.code.strip():
            issue("python_code_required", "Добавьте Python-код.", key)
        if node.type == "llm":
            if not node.agent_id:
                issue("agent_required", "Выберите агента.", key, "agent_id")
            if set(node.allowed_tools) - set(definition.allowed_tools):
                issue("tool_not_allowed", "Инструмент не разрешён сценарием.", key)
            result_spec = node.outputs.get("result")
            if (
                result_spec is None
                or (node.output_format == "text" and result_spec.type != "string")
                or (
                    node.output_format == "json"
                    and result_spec is not None
                    and result_spec.type != "json"
                )
            ):
                issue(
                    "llm_output_type",
                    "Выход result должен иметь тип string для текста или json для структурированного ответа.",
                    key,
                )
        for name, spec in node.outputs.items():
            if spec.json_schema is not None:
                try:
                    check_schema(spec.json_schema)
                except WorkflowError as exc:
                    issue(exc.code, str(exc), key, name)
        for name, binding in node.inputs.items():
            try:
                if binding.source == "constant":
                    validate_value(
                        binding.value, ValueSpec(type=binding.type, required=binding.required)
                    )
                    continue
                if not binding.required:
                    validate_value(binding.default, ValueSpec(type=binding.type, required=False))
                source_spec = _binding_spec(binding, definition, graph)
                if source_spec is None:
                    issue("unknown_input_reference", "Не найден выход для этой связи.", key, name)
                elif binding.type != "json" and source_spec.type != binding.type:
                    issue(
                        "input_type_mismatch",
                        f"Ожидался {binding.type}, получен {source_spec.type}.",
                        key,
                        name,
                    )
                if binding.source == "step":
                    source = binding.node_id or ""
                    if source not in ancestors[key]:
                        issue(
                            "input_not_ancestor", "Источник должен предшествовать шагу.", key, name
                        )
                    elif binding.required and not all(
                        any(source_path <= target_path for source_path in paths[source])
                        for target_path in paths[key]
                    ):
                        issue(
                            "possibly_skipped_input",
                            "Выход может отсутствовать: используйте необязательный вход объединения.",
                            key,
                            name,
                        )
            except WorkflowError as exc:
                issue(exc.code, str(exc), key, name)
    for name, spec in definition.parameters.items():
        try:
            if spec.json_schema is not None:
                check_schema(spec.json_schema)
            if spec.default is not None:
                validate_value(spec.default, spec)
        except WorkflowError as exc:
            issue(exc.code, str(exc), field=name)
    if ends and paths.get(ends[0]) != {frozenset()}:
        issue(
            "conditional_result",
            "Результат должен быть достижим для обеих ветвей условия.",
            ends[0],
        )
    return WorkflowValidationResult(valid=not issues, issues=issues)


def evaluate_condition(node: ConditionNode, inputs: dict[str, Any]) -> bool:
    checks: list[bool] = []
    for rule in node.rules:
        left, right = inputs[rule.input], rule.value
        try:
            if rule.operator == "eq":
                outcome = type(left) is type(right) and left == right
            elif rule.operator == "ne":
                outcome = type(left) is not type(right) or left != right
            elif rule.operator == "is_empty":
                outcome = left is None or left == "" or left == [] or left == {}
            elif rule.operator == "contains":
                outcome = isinstance(left, str | list | dict) and right in left
            else:
                if isinstance(left, bool) or isinstance(right, bool):
                    raise WorkflowError("condition_type_mismatch", rule.input)
                operations = {
                    "gt": operator.gt,
                    "gte": operator.ge,
                    "lt": operator.lt,
                    "lte": operator.le,
                }
                outcome = operations[rule.operator](left, right)
        except (TypeError, KeyError) as exc:
            raise WorkflowError("condition_type_mismatch", rule.input) from exc
        checks.append(bool(outcome))
    return all(checks) if node.match == "all" else any(checks)


def isolated_step_definition(
    definition: ScenarioWorkflowDefinition, node_id: str, inputs: dict[str, Any]
) -> ScenarioWorkflowDefinition:
    """Validate explicit step inputs independently of unfinished surrounding nodes."""
    from rhetorica_backend.contracts.scenario_workflows import InputNode, ResultNode

    if len({node.id for node in definition.nodes}) != len(definition.nodes):
        raise WorkflowError("duplicate_node_id")
    node = next((node for node in definition.nodes if node.id == node_id), None)
    if node is None:
        raise WorkflowError("scenario_step_not_found", status_code=404)
    start = InputNode(id="TestStart" if node_id != "TestStart" else "TestInput", title="Test input")
    end = ResultNode(
        id="TestResult" if node_id != "TestResult" else "TestOutput", title="Test output"
    )
    if node.type == "input":
        return ScenarioWorkflowDefinition(
            parameters=definition.parameters,
            nodes=[node, end],
            edges=[ScenarioEdge(source=node.id, target=end.id)],
        )
    unknown = set(inputs) - set(node.inputs)
    if unknown:
        raise WorkflowError("step_test_input_names_mismatch", ", ".join(sorted(unknown)))
    constants = {}
    for name, binding in node.inputs.items():
        value = inputs.get(name, binding.default)
        validate_value(value, ValueSpec(type=binding.type, required=binding.required))
        constants[name] = binding.model_copy(
            update={"source": "constant", "node_id": None, "path": [], "value": value}
        )
    node = node.model_copy(update={"inputs": constants})
    nodes: list[ScenarioNode] = [start, node] if node.type == "result" else [start, node, end]
    edges = [ScenarioEdge(source=start.id, target=node.id)]
    if node.type == "condition":
        edges.extend([
            ScenarioEdge(source=node.id, target=end.id, branch="true"),
            ScenarioEdge(source=node.id, target=end.id, branch="false"),
        ])
    elif node.type != "result":
        edges.append(ScenarioEdge(source=node.id, target=end.id))
    return ScenarioWorkflowDefinition(
        nodes=nodes, edges=edges, allowed_tools=definition.allowed_tools
    )
