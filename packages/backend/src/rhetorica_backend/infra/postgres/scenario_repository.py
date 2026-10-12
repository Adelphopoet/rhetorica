from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rhetorica_backend.contracts.scenarios import (
    ScenarioConnectorBindings,
    ScenarioToolPolicy,
)
from rhetorica_backend.infra.postgres.scenario_models import (
    ScenarioDefinitionOrm,
    ScenarioRunOrm,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import or_, select

_LEGACY_CONNECTOR_BINDING_KEYS = frozenset({"metadata_connector_name"})


class ScenarioDefinitionItem:
    def __init__(
        self,
        scenario_id: str,
        slug: str,
        workspace_id: str,
        owner_user_id: str | None,
        scope: str,
        title: str,
        summary: str,
        description: str,
        input_schema: dict[str, Any],
        prompt_template: str,
        tool_policy: ScenarioToolPolicy,
        default_connector_bindings: ScenarioConnectorBindings,
        status: str,
        version: int,
        created_at: datetime,
        updated_at: datetime,
        published_at: datetime | None,
    ) -> None:
        self.id = scenario_id
        self.slug = slug
        self.workspace_id = workspace_id
        self.owner_user_id = owner_user_id
        self.scope = scope
        self.title = title
        self.summary = summary
        self.description = description
        self.input_schema = input_schema
        self.prompt_template = prompt_template
        self.tool_policy = tool_policy
        self.default_connector_bindings = default_connector_bindings
        self.status = status
        self.version = version
        self.created_at = created_at
        self.updated_at = updated_at
        self.published_at = published_at


class ScenarioRunItem:
    def __init__(
        self,
        run_id: str,
        scenario_definition_id: str,
        workspace_id: str,
        owner_user_id: str,
        resolved_version: int,
        input_payload: dict[str, Any],
        conversation_id: str | None,
        created_at: datetime,
    ) -> None:
        self.id = run_id
        self.scenario_definition_id = scenario_definition_id
        self.workspace_id = workspace_id
        self.owner_user_id = owner_user_id
        self.resolved_version = resolved_version
        self.input_payload = input_payload
        self.conversation_id = conversation_id
        self.created_at = created_at


class ScenarioRepository:
    def __init__(self, db: DatabaseRuntime):
        self._db = db

    def get_definition(
        self,
        scenario_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> ScenarioDefinitionItem | None:
        with self._db.session() as session:
            stmt = self._visible_definition_stmt(workspace_id, owner_user_id).where(
                ScenarioDefinitionOrm.id == scenario_id
            )
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return self._to_definition_item(row)

    def get_definition_for_owner(
        self,
        scenario_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> ScenarioDefinitionItem | None:
        with self._db.session() as session:
            stmt = select(ScenarioDefinitionOrm).where(
                ScenarioDefinitionOrm.id == scenario_id,
                ScenarioDefinitionOrm.workspace_id == workspace_id,
                or_(
                    ScenarioDefinitionOrm.scope == "official",
                    ScenarioDefinitionOrm.owner_user_id == owner_user_id,
                ),
            )
            row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return self._to_definition_item(row)

    def get_definition_by_slug(
        self,
        workspace_id: str,
        slug: str,
    ) -> ScenarioDefinitionItem | None:
        with self._db.session() as session:
            row = session.execute(
                select(ScenarioDefinitionOrm).where(
                    ScenarioDefinitionOrm.workspace_id == workspace_id,
                    ScenarioDefinitionOrm.slug == slug,
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return self._to_definition_item(row)

    def list_definitions(
        self,
        workspace_id: str,
        owner_user_id: str,
        include_archived: bool = False,
    ) -> list[ScenarioDefinitionItem]:
        with self._db.session() as session:
            stmt = self._visible_definition_stmt(workspace_id, owner_user_id)
            if not include_archived:
                stmt = stmt.where(ScenarioDefinitionOrm.status != "archived")
            rows = session.execute(
                stmt.order_by(
                    ScenarioDefinitionOrm.scope.asc(),
                    ScenarioDefinitionOrm.title.asc(),
                    ScenarioDefinitionOrm.updated_at.desc(),
                )
            ).scalars()
            return [self._to_definition_item(row) for row in rows]

    def create_definition(
        self,
        *,
        slug: str,
        workspace_id: str,
        owner_user_id: str | None,
        scope: str,
        title: str,
        summary: str,
        description: str,
        input_schema: dict[str, Any],
        prompt_template: str,
        tool_policy: ScenarioToolPolicy,
        default_connector_bindings: ScenarioConnectorBindings,
        status: str,
    ) -> ScenarioDefinitionItem:
        now = datetime.now(tz=UTC)
        published_at = now if status == "published" else None
        with self._db.session() as session:
            row = ScenarioDefinitionOrm(
                slug=slug,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                scope=scope,
                title=title,
                summary=summary,
                description=description,
                input_schema=input_schema,
                prompt_template=prompt_template,
                tool_policy=tool_policy.model_dump(mode="json"),
                default_connector_bindings=default_connector_bindings.model_dump(mode="json"),
                status=status,
                version=1,
                created_at=now,
                updated_at=now,
                published_at=published_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_definition_item(row)

    def update_definition(
        self,
        scenario_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        prompt_template: str | None = None,
        tool_policy: ScenarioToolPolicy | None = None,
        default_connector_bindings: ScenarioConnectorBindings | None = None,
        status: str | None = None,
    ) -> ScenarioDefinitionItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(ScenarioDefinitionOrm).where(ScenarioDefinitionOrm.id == scenario_id)
            ).scalar_one_or_none()
            if row is None:
                return None

            if title is not None:
                row.title = title
            if summary is not None:
                row.summary = summary
            if description is not None:
                row.description = description
            if input_schema is not None:
                row.input_schema = input_schema
            if prompt_template is not None:
                row.prompt_template = prompt_template
            if tool_policy is not None:
                row.tool_policy = tool_policy.model_dump(mode="json")
            if default_connector_bindings is not None:
                row.default_connector_bindings = default_connector_bindings.model_dump(mode="json")
            if status is not None and status != row.status:
                row.status = status
                if status == "published":
                    row.published_at = now

            row.version += 1
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._to_definition_item(row)

    def archive_definition(self, scenario_id: str) -> ScenarioDefinitionItem | None:
        return self.update_definition(scenario_id, status="archived")

    def create_run(
        self,
        *,
        scenario_definition_id: str,
        workspace_id: str,
        owner_user_id: str,
        resolved_version: int,
        input_payload: dict[str, Any],
        conversation_id: str | None,
    ) -> ScenarioRunItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = ScenarioRunOrm(
                scenario_definition_id=scenario_definition_id,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                resolved_version=resolved_version,
                input_payload=input_payload,
                conversation_id=conversation_id,
                created_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_run_item(row)

    @staticmethod
    def _visible_definition_stmt(workspace_id: str, owner_user_id: str):
        return select(ScenarioDefinitionOrm).where(
            ScenarioDefinitionOrm.workspace_id == workspace_id,
            or_(
                (
                    (ScenarioDefinitionOrm.scope == "official")
                    & (ScenarioDefinitionOrm.status == "published")
                ),
                ScenarioDefinitionOrm.owner_user_id == owner_user_id,
            ),
        )

    @staticmethod
    def _to_definition_item(row: ScenarioDefinitionOrm) -> ScenarioDefinitionItem:
        return ScenarioDefinitionItem(
            scenario_id=row.id,
            slug=row.slug,
            workspace_id=row.workspace_id,
            owner_user_id=row.owner_user_id,
            scope=row.scope,
            title=row.title,
            summary=row.summary,
            description=row.description,
            input_schema=row.input_schema or {},
            prompt_template=row.prompt_template,
            tool_policy=ScenarioToolPolicy.model_validate(row.tool_policy or {}),
            default_connector_bindings=ScenarioRepository._load_connector_bindings(
                row.default_connector_bindings
            ),
            status=row.status,
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
            published_at=row.published_at,
        )

    @staticmethod
    def _load_connector_bindings(value: dict[str, Any] | None) -> ScenarioConnectorBindings:
        normalized = dict(value or {})
        for key in _LEGACY_CONNECTOR_BINDING_KEYS:
            normalized.pop(key, None)
        return ScenarioConnectorBindings.model_validate(normalized)

    @staticmethod
    def _to_run_item(row: ScenarioRunOrm) -> ScenarioRunItem:
        return ScenarioRunItem(
            run_id=row.id,
            scenario_definition_id=row.scenario_definition_id,
            workspace_id=row.workspace_id,
            owner_user_id=row.owner_user_id,
            resolved_version=row.resolved_version,
            input_payload=row.input_payload or {},
            conversation_id=row.conversation_id,
            created_at=row.created_at,
        )
