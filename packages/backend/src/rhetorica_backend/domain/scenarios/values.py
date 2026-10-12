"""Strict values, local JSON schemas and explicit input bindings."""

from __future__ import annotations

from typing import Any, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]
from rhetorica_backend.contracts.scenario_workflows import InputBinding, ValueSpec
from rhetorica_backend.domain.scenarios.errors import WorkflowError


def check_schema(schema: dict[str, Any]) -> None:
    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise WorkflowError("external_schema_reference_forbidden")
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)

    inspect(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise WorkflowError("invalid_json_schema", exc.message) from exc


def validate_schema(value: Any, schema: dict[str, Any]) -> None:
    check_schema(schema)
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        raise WorkflowError("output_schema_mismatch", f"{path}: {exc.message}") from exc


def validate_value(value: Any, spec: ValueSpec) -> Any:
    if value is None and not spec.required:
        return value
    valid = {
        "string": isinstance(value, str),
        "number": isinstance(value, float | int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "json": value is not None or not spec.required,
        "table": isinstance(value, dict) and isinstance(value.get("execution_id"), str),
        "file": isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("filename"), str),
        "files": isinstance(value, list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("filename"), str)
            for item in value
        ),
    }[spec.type]
    if not valid:
        raise WorkflowError("value_type_mismatch", f"Expected {spec.type}")
    if spec.json_schema is not None:
        validate_schema(value, spec.json_schema)
    return value


def validate_parameters(values: dict[str, Any], specs: dict[str, ValueSpec]) -> dict[str, Any]:
    unknown = set(values) - set(specs)
    if unknown:
        raise WorkflowError("unknown_parameter", ", ".join(sorted(unknown)))
    result: dict[str, Any] = {}
    for name, spec in specs.items():
        if name not in values and spec.required and spec.default is None:
            raise WorkflowError("missing_parameter", name)
        result[name] = validate_value(values.get(name, spec.default), spec)
    return result


def select_path(value: Any, path: list[str | int]) -> Any:
    for part in path:
        if (isinstance(value, dict) and isinstance(part, str) and part in value) or (
            isinstance(value, list) and isinstance(part, int) and 0 <= part < len(value)
        ):
            value = cast(Any, value)[part]
        else:
            raise WorkflowError("input_reference_missing", ".".join(map(str, path)))
    return value


def resolve_bindings(
    bindings: dict[str, InputBinding],
    parameters: dict[str, Any],
    outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, binding in bindings.items():
        try:
            if binding.source == "constant":
                value = binding.value
            elif binding.source == "input":
                value = select_path(parameters, binding.path)
            else:
                value = select_path(outputs, [binding.node_id or "", *binding.path])
        except WorkflowError:
            if binding.required:
                raise
            value = binding.default
        result[name] = validate_value(
            value, ValueSpec(type=binding.type, required=binding.required)
        )
    return result
