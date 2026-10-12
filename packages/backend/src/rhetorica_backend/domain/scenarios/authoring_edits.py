"""Atomic, bounded edits to a workflow, addressed by stable node IDs."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Literal

from pydantic import Field
from rhetorica_backend.contracts.scenario_workflows import (
    ScenarioWorkflowDefinition,
    WorkflowContract,
)
from rhetorica_backend.domain.scenarios.errors import WorkflowError


class WorkflowEdit(WorkflowContract):
    op: Literal["add", "replace", "remove"]
    path: str = Field(
        min_length=1, max_length=512, description="JSON Pointer, e.g. /nodes/agent/instructions"
    )
    value: Any = None


class WorkflowEditProposal(WorkflowContract):
    summary: str = Field(
        min_length=1, max_length=2000, description="Кратко объясни пользователю, что изменено"
    )
    operations: list[WorkflowEdit] = Field(max_length=100)
    complete: bool = Field(
        description="true только когда весь запрос выполнен; false — продолжить сборку следующим ответом"
    )


def editable_workflow(graph: ScenarioWorkflowDefinition) -> dict[str, Any]:
    value = graph.model_dump(mode="json", exclude_none=True)
    value["nodes"] = {node["id"]: node for node in value["nodes"]}
    return value


def _array_index(value: str, length: int, *, insert: bool = False) -> int:
    if insert and value == "-":
        return length
    if not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise WorkflowError("invalid_edit_index", value)
    index = int(value)
    if index >= length + int(insert):
        raise WorkflowError("edit_index_out_of_range", value)
    return index


def apply_workflow_edits(
    graph: ScenarioWorkflowDefinition, proposal: WorkflowEditProposal
) -> ScenarioWorkflowDefinition:
    document = editable_workflow(graph)
    for edit in proposal.operations:
        if not edit.path.startswith("/") or re.search(r"~(?![01])", edit.path):
            raise WorkflowError("invalid_edit_path", edit.path)
        parts = [part.replace("~1", "/").replace("~0", "~") for part in edit.path[1:].split("/")]
        if parts[0] not in {"nodes", "edges", "parameters", "allowed_tools"}:
            raise WorkflowError("edit_path_forbidden", edit.path)
        # Node IDs are keys, not array indexes; never replace the whole node collection.
        if parts[0] == "nodes" and (len(parts) < 2 or parts[2:3] == ["id"]):
            raise WorkflowError("edit_node_id_required", edit.path)
        parent: Any = document
        for part in parts[:-1]:
            if isinstance(parent, dict) and part in parent:
                parent = parent[part]
            elif isinstance(parent, list):
                parent = parent[_array_index(part, len(parent))]
            else:
                raise WorkflowError("edit_target_missing", edit.path)
        key = parts[-1]
        if edit.op != "remove" and "value" not in edit.model_fields_set:
            raise WorkflowError("edit_value_required", edit.path)
        value = deepcopy(edit.value)
        if isinstance(parent, dict):
            if edit.op != "add" and key not in parent:
                raise WorkflowError("edit_target_missing", edit.path)
            if edit.op == "remove":
                del parent[key]
            else:
                parent[key] = value
        elif isinstance(parent, list):
            index = _array_index(key, len(parent), insert=edit.op == "add")
            if edit.op == "add":
                parent.insert(index, value)
            elif edit.op == "remove":
                parent.pop(index)
            else:
                parent[index] = value
        else:
            raise WorkflowError("edit_target_not_container", edit.path)
    nodes = document.get("nodes")
    if not isinstance(nodes, dict):
        raise WorkflowError("edit_nodes_required")
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("id", node_id) != node_id:
            raise WorkflowError("edit_node_id_mismatch", node_id)
        node["id"] = node_id
    document["nodes"] = list(nodes.values())
    if len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode()) > 1024 * 1024:
        raise WorkflowError("scenario_json_limit")
    result = ScenarioWorkflowDefinition.model_validate(document)
    if not proposal.complete and result == graph:
        raise WorkflowError("empty_edit_batch", "Добавь следующую часть или заверши complete:true.")
    return result
