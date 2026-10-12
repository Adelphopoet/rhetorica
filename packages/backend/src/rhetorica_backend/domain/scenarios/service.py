from __future__ import annotations

import re
from typing import Any

from rhetorica_backend.contracts.chat import ChatActorContext, ChatAskRequest
from rhetorica_backend.contracts.scenarios import (
    CreateScenarioRequest,
    ScenarioConnectorBindings,
    ScenarioDefinitionItem,
    ScenarioRunRequest,
    ScenarioRunResponse,
    ScenarioScope,
    ScenarioStatus,
    ScenarioToolPolicy,
    UpdateScenarioRequest,
)
from rhetorica_backend.domain.chat.orchestration.factory import ChatServiceFactory
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.scenario_repository import (
    ScenarioDefinitionItem as ScenarioDefinitionRecord,
)
from rhetorica_backend.infra.postgres.scenario_repository import ScenarioRepository
from rhetorica_backend.infra.postgres.user_repository import UserAccountItem

_PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")


class ScenarioAccessError(ValueError):
    pass


class ScenarioValidationError(ValueError):
    pass


class ScenarioCatalogService:
    def __init__(
        self,
        *,
        scenario_repository: ScenarioRepository,
        chat_repository: ChatRepository,
        chat_factory: ChatServiceFactory,
        db_connector_repository: ConnectorRepository,
        llm_connector_repository: LlmConnectorRepository,
    ) -> None:
        self._scenario_repository = scenario_repository
        self._chat_repository = chat_repository
        self._chat_factory = chat_factory
        self._db_connector_repository = db_connector_repository
        self._llm_connector_repository = llm_connector_repository

    def list_definitions(
        self,
        *,
        workspace_id: str,
        current_user: UserAccountItem,
    ) -> list[ScenarioDefinitionItem]:
        definitions = self._scenario_repository.list_definitions(
            workspace_id=workspace_id,
            owner_user_id=current_user.id,
        )
        return [self._to_contract(item) for item in definitions]

    def create_definition(
        self,
        request: CreateScenarioRequest,
        *,
        current_user: UserAccountItem,
    ) -> ScenarioDefinitionItem:
        scope = self._normalize_scope(request.scope)
        status = self._normalize_status(request.status)
        owner_user_id = self._resolve_owner(scope=scope, current_user=current_user)
        self._validate_input_schema(request.input_schema)
        self._validate_policy(request.tool_policy)
        self._validate_bindings(request.workspace_id, request.default_connector_bindings)
        slug = self._ensure_unique_slug(
            workspace_id=request.workspace_id,
            title=request.title,
            requested_slug=request.slug,
        )

        created = self._scenario_repository.create_definition(
            slug=slug,
            workspace_id=request.workspace_id,
            owner_user_id=owner_user_id,
            scope=scope,
            title=request.title,
            summary=request.summary,
            description=request.description,
            input_schema=request.input_schema,
            prompt_template=request.prompt_template,
            tool_policy=request.tool_policy,
            default_connector_bindings=request.default_connector_bindings,
            status=status,
        )
        return self._to_contract(created)

    def update_definition(
        self,
        scenario_id: str,
        request: UpdateScenarioRequest,
        *,
        current_user: UserAccountItem,
    ) -> ScenarioDefinitionItem:
        existing = self._require_editable_definition(
            scenario_id=scenario_id,
            workspace_id=request.workspace_id,
            current_user=current_user,
        )
        if request.input_schema is not None:
            self._validate_input_schema(request.input_schema)
        if request.tool_policy is not None:
            self._validate_policy(request.tool_policy)
        if request.default_connector_bindings is not None:
            self._validate_bindings(request.workspace_id, request.default_connector_bindings)

        next_status = self._normalize_status(request.status) if request.status is not None else None
        updated = self._scenario_repository.update_definition(
            scenario_id=existing.id,
            title=request.title,
            summary=request.summary,
            description=request.description,
            input_schema=request.input_schema,
            prompt_template=request.prompt_template,
            tool_policy=request.tool_policy,
            default_connector_bindings=request.default_connector_bindings,
            status=next_status,
        )
        if updated is None:
            raise ScenarioAccessError("Scenario not found")
        return self._to_contract(updated)

    def archive_definition(
        self,
        scenario_id: str,
        *,
        workspace_id: str,
        current_user: UserAccountItem,
    ) -> ScenarioDefinitionItem:
        existing = self._require_editable_definition(
            scenario_id=scenario_id,
            workspace_id=workspace_id,
            current_user=current_user,
        )
        archived = self._scenario_repository.archive_definition(existing.id)
        if archived is None:
            raise ScenarioAccessError("Scenario not found")
        return self._to_contract(archived)

    def run_definition(
        self,
        scenario_id: str,
        request: ScenarioRunRequest,
        *,
        current_user: UserAccountItem,
    ) -> ScenarioRunResponse:
        definition = self._scenario_repository.get_definition(
            scenario_id=scenario_id,
            workspace_id=request.workspace_id,
            owner_user_id=current_user.id,
        )
        if definition is None or definition.status == ScenarioStatus.ARCHIVED:
            raise ScenarioAccessError("Scenario not found")

        validated_inputs = self._validate_inputs(
            input_schema=definition.input_schema,
            provided_inputs=request.inputs,
        )
        rendered_prompt = self._render_prompt(
            prompt_template=definition.prompt_template,
            inputs=validated_inputs,
        )

        conversation_id = request.conversation_id
        if conversation_id is None:
            conversation = self._chat_repository.create_conversation(
                workspace_id=request.workspace_id,
                title=definition.title,
                owner_user_id=current_user.id,
            )
            conversation_id = conversation.id
        else:
            conversation = self._chat_repository.get_conversation(
                conversation_id=conversation_id,
                owner_user_id=current_user.id,
            )
            if conversation is None:
                raise ScenarioAccessError("Conversation not found")
            if conversation.workspace_id != request.workspace_id:
                raise ScenarioAccessError("Conversation does not belong to workspace")

        allow_run_sql = definition.tool_policy.allow_run_sql
        if definition.default_connector_bindings.run_sql is False:
            allow_run_sql = False

        response = self._chat_factory.create_orchestrator(current_user_id=current_user.id).ask(
            ChatAskRequest(
                question=rendered_prompt,
                workspace_id=request.workspace_id,
                conversation_id=conversation_id,
                llm_connector_name=definition.default_connector_bindings.llm_connector_name,
                database_connector_name=definition.default_connector_bindings.database_connector_name,
                run_sql=allow_run_sql,
                actor_context=ChatActorContext(
                    source="web",
                    login=current_user.login,
                    display_name=current_user.display_name,
                    role=current_user.role,
                    auth_provider=current_user.auth_provider,
                    workspace_id=current_user.workspace_id,
                ),
            )
        )

        latest_run = self._scenario_repository.create_run(
            scenario_definition_id=definition.id,
            workspace_id=request.workspace_id,
            owner_user_id=current_user.id,
            resolved_version=definition.version,
            input_payload=validated_inputs,
            conversation_id=response.conversation_id or conversation_id,
        )

        return ScenarioRunResponse(
            scenario_run_id=latest_run.id,
            scenario_definition_id=definition.id,
            rendered_prompt=rendered_prompt,
            **response.model_dump(),
        )

    def _require_editable_definition(
        self,
        *,
        scenario_id: str,
        workspace_id: str,
        current_user: UserAccountItem,
    ) -> ScenarioDefinitionRecord:
        existing = self._scenario_repository.get_definition_for_owner(
            scenario_id=scenario_id,
            workspace_id=workspace_id,
            owner_user_id=current_user.id,
        )
        if existing is None:
            raise ScenarioAccessError("Scenario not found")
        if existing.scope == ScenarioScope.OFFICIAL and not current_user.is_admin:
            raise ScenarioAccessError("Official scenarios require platform owner access")
        if existing.scope == ScenarioScope.PERSONAL and existing.owner_user_id != current_user.id:
            raise ScenarioAccessError("Scenario does not belong to current user")
        return existing

    @staticmethod
    def _normalize_scope(value: str) -> str:
        if value not in {ScenarioScope.OFFICIAL, ScenarioScope.PERSONAL}:
            raise ScenarioValidationError("Unsupported scenario scope")
        return value

    @staticmethod
    def _normalize_status(value: str) -> str:
        if value not in {
            ScenarioStatus.DRAFT,
            ScenarioStatus.PUBLISHED,
            ScenarioStatus.ARCHIVED,
        }:
            raise ScenarioValidationError("Unsupported scenario status")
        return value

    @staticmethod
    def _resolve_owner(*, scope: str, current_user: UserAccountItem) -> str | None:
        if scope == ScenarioScope.OFFICIAL:
            if not current_user.is_admin:
                raise ScenarioAccessError("Official scenarios require platform owner access")
            return None
        return current_user.id

    @staticmethod
    def _validate_input_schema(input_schema: dict[str, Any]) -> None:
        schema_type = input_schema.get("type", "object")
        if schema_type != "object":
            raise ScenarioValidationError("Only object input_schema is supported in MVP")
        properties = input_schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ScenarioValidationError("input_schema.properties must be an object")
        required = input_schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise ScenarioValidationError("input_schema.required must be a list of field names")
        supported_types = {"string", "number", "integer", "boolean"}
        for field_name, spec in properties.items():
            if not isinstance(spec, dict):
                raise ScenarioValidationError(
                    f"input_schema property '{field_name}' must be an object"
                )
            field_type = spec.get("type", "string")
            if field_type not in supported_types:
                raise ScenarioValidationError(
                    f"input_schema property '{field_name}' has unsupported type '{field_type}'"
                )

    @staticmethod
    def _validate_policy(tool_policy: ScenarioToolPolicy) -> None:
        if tool_policy.sql_guard_required is not True:
            raise ScenarioValidationError("Scenarios cannot disable SQL guard")

    def _validate_bindings(
        self,
        workspace_id: str,
        bindings: ScenarioConnectorBindings,
    ) -> None:
        if bindings.database_connector_name:
            connection = self._db_connector_repository.get_connection(
                workspace_id=workspace_id,
                name=bindings.database_connector_name,
            )
            if connection is None:
                raise ScenarioValidationError("Unknown database connector in default bindings")
        if bindings.llm_connector_name:
            connection = self._llm_connector_repository.get_connection(
                workspace_id=workspace_id,
                name=bindings.llm_connector_name,
            )
            if connection is None:
                raise ScenarioValidationError("Unknown LLM connector in default bindings")

    def _ensure_unique_slug(
        self,
        *,
        workspace_id: str,
        title: str,
        requested_slug: str | None,
    ) -> str:
        base_slug = self._slugify(requested_slug or title)
        if self._scenario_repository.get_definition_by_slug(workspace_id, base_slug) is None:
            return base_slug
        index = 2
        while True:
            candidate = f"{base_slug}-{index}"
            if self._scenario_repository.get_definition_by_slug(workspace_id, candidate) is None:
                return candidate
            index += 1

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return normalized or "scenario"

    @staticmethod
    def _validate_inputs(
        *,
        input_schema: dict[str, Any],
        provided_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        properties = input_schema.get("properties", {})
        required = set(input_schema.get("required", []))
        validated: dict[str, Any] = {}
        for field_name, spec in properties.items():
            if field_name in provided_inputs:
                value = provided_inputs[field_name]
            elif "default" in spec:
                value = spec["default"]
            else:
                value = None

            if value is None:
                if field_name in required:
                    raise ScenarioValidationError(f"Missing required input '{field_name}'")
                continue

            field_type = spec.get("type", "string")
            if field_type == "string":
                if not isinstance(value, str):
                    raise ScenarioValidationError(f"Input '{field_name}' must be a string")
            elif field_type == "number":
                if not isinstance(value, int | float) or isinstance(value, bool):
                    raise ScenarioValidationError(f"Input '{field_name}' must be a number")
            elif field_type == "integer":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ScenarioValidationError(f"Input '{field_name}' must be an integer")
            elif field_type == "boolean" and not isinstance(value, bool):
                raise ScenarioValidationError(f"Input '{field_name}' must be a boolean")
            validated[field_name] = value
        return validated

    @staticmethod
    def _render_prompt(prompt_template: str, inputs: dict[str, Any]) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in inputs:
                raise ScenarioValidationError(f"Missing template input '{key}'")
            value = inputs[key]
            return str(value)

        rendered = _PLACEHOLDER_RE.sub(replace, prompt_template)
        if "{{" in rendered or "}}" in rendered:
            raise ScenarioValidationError("Prompt template contains unresolved placeholders")
        return rendered.strip()

    @staticmethod
    def _to_contract(item: ScenarioDefinitionRecord) -> ScenarioDefinitionItem:
        return ScenarioDefinitionItem(
            id=item.id,
            slug=item.slug,
            workspace_id=item.workspace_id,
            owner_user_id=item.owner_user_id,
            scope=item.scope,
            title=item.title,
            summary=item.summary,
            description=item.description,
            input_schema=item.input_schema,
            prompt_template=item.prompt_template,
            tool_policy=item.tool_policy,
            default_connector_bindings=item.default_connector_bindings,
            status=item.status,
            version=item.version,
            published_at=item.published_at.isoformat() if item.published_at else None,
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
        )
