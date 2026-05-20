"""Pre-execution SQL optimization advisor."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

import clickhouse_connect  # type: ignore[import-untyped]
from rhetorica_backend.contracts.chat import (
    OptimizationAction,
    OptimizationCitation,
    OptimizationFinding,
    OptimizationTrace,
)
from rhetorica_backend.contracts.execution import (
    EngineConnectionConfig,
    sql_dialect_for_engine,
)
from rhetorica_backend.contracts.workspace_skills import WorkspaceSkillItem
from rhetorica_backend.domain.chat.sql.semantic_preflight import (
    SemanticSqlPreflightService,
)
from rhetorica_backend.domain.workspace_skills.runtime import find_rule_asset
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

logger = logging.getLogger(__name__)

_RULE_JOIN_FILTER_BEFORE = "query-join-filter-before"
_RULE_JOIN_ALTERNATIVES = "query-join-consider-alternatives"
_RULE_ORDER_BY_PREFIX = "schema-pk-filter-on-orderby"
_RULE_SKIP_INDEX = "query-index-skipping-indices"
_RULE_INCREMENTAL_MV = "query-mv-incremental"
_OPTIMIZER_CLICKHOUSE_CONNECT_TIMEOUT = 1
_OPTIMIZER_CLICKHOUSE_SEND_RECEIVE_TIMEOUT = 2
_OPTIMIZER_CLICKHOUSE_QUERY_RETRIES = 0


@dataclass(frozen=True)
class TableOptimizationMetadata:
    fqn: str
    database: str
    table: str
    engine: str | None
    ddl: str | None
    sorting_key: list[str]
    primary_key: list[str]
    partition_key: str | None
    skip_indexes: list[str]
    columns: dict[str, str]


@dataclass(frozen=True)
class ClickHouseInspectionResult:
    tables: list[TableOptimizationMetadata]
    evidence_used: list[str]


@dataclass(frozen=True)
class SqlOptimizationOutcome:
    sql_for_execution: str
    trace: OptimizationTrace


class SqlRewriteFormatter:
    """Formats optimizer-owned SQL ASTs without string heuristics."""

    def format_expression(self, expression: exp.Expression, *, dialect: str) -> str:
        return expression.sql(dialect=dialect, pretty=True)


class SqlOptimizationService:
    """Rule-first SQL optimization path with optional workspace skill citations."""

    def __init__(
        self,
        *,
        workspace_skill_repository: WorkspaceSkillRepository | None = None,
        sql_formatter: SqlRewriteFormatter | None = None,
    ) -> None:
        self._workspace_skill_repository = workspace_skill_repository
        self._sql_formatter = sql_formatter or SqlRewriteFormatter()

    def optimize(
        self,
        *,
        workspace_id: str,
        connection: EngineConnectionConfig,
        sql: str,
        referenced_tables: list[str],
        semantic_preflight_service: SemanticSqlPreflightService,
        context_tables: list[str],
        metadata_table_columns: dict[str, set[str]],
        selected_connector_name: str,
    ) -> SqlOptimizationOutcome:
        trace = OptimizationTrace(
            optimizer_used=False,
            optimizer_provider=None,
            input_sql=sql,
            optimized_sql_draft=sql,
            input_sql_hash=self._sql_hash(sql),
            optimized_sql_hash=self._sql_hash(sql),
            accepted_or_rejected="not_used",
            applied=False,
            engine_scope=connection.engine,
        )
        if not self._supports_engine(connection.engine):
            trace.warnings.append("sql_optimizer_engine_not_supported")
            return SqlOptimizationOutcome(sql_for_execution=sql, trace=trace)

        skills = self._match_skills(
            workspace_id=workspace_id,
            engine_scope=connection.engine,
        )
        if not skills:
            trace.warnings.append("sql_optimizer_no_matching_skills")
            return SqlOptimizationOutcome(sql_for_execution=sql, trace=trace)

        dialect = sql_dialect_for_engine(connection.engine)
        parsed = self._parse(sql=sql, dialect=dialect)
        if parsed is None:
            trace.warnings.append("sql_optimizer_parse_failed")
            return SqlOptimizationOutcome(sql_for_execution=sql, trace=trace)

        inspection = (
            self._inspect_clickhouse_tables(
                connection=connection,
                referenced_tables=referenced_tables,
            )
            if connection.engine == "clickhouse"
            and self._requires_clickhouse_table_inspection(parsed)
            else ClickHouseInspectionResult(tables=[], evidence_used=[])
        )
        table_metadata = inspection.tables
        explain_summary = (
            self._explain_indexes(connection=connection, sql=sql)
            if connection.engine == "clickhouse" and self._requires_clickhouse_explain(parsed)
            else None
        )
        findings: list[OptimizationFinding] = []
        actions: list[OptimizationAction] = []
        warnings: list[str] = []
        rules_triggered: list[str] = []
        evidence_used = list(inspection.evidence_used)
        if explain_summary:
            evidence_used.append("explain:indexes=1")

        join_finding, pushed_sql = self._analyze_join_filter_pushdown(
            parsed,
            dialect=dialect,
        )
        if join_finding is not None:
            findings.append(join_finding)
            rules_triggered.append(_RULE_JOIN_FILTER_BEFORE)
            if pushed_sql is not None and pushed_sql != sql:
                revalidated = semantic_preflight_service.check(
                    sql=pushed_sql,
                    dialect=dialect,
                    engine=connection.engine,
                    context_tables=context_tables,
                    metadata_table_columns=metadata_table_columns,
                    requested_connector_name=selected_connector_name,
                    selected_connector_name=selected_connector_name,
                )
                if revalidated.status == "pass":
                    actions.append(
                        OptimizationAction(
                            action_id="pushdown_predicates",
                            kind="rewrite",
                            title="Push down single-table predicates before INNER JOIN",
                            summary="Safe single-table predicates were moved closer to source scans.",
                            sql=pushed_sql,
                            applied=True,
                            citations=self._rule_citations(_RULE_JOIN_FILTER_BEFORE, skills),
                        )
                    )
                else:
                    warnings.append("sql_optimizer_rewrite_reverted_preflight")
                    pushed_sql = None

        mirrored_date_filter_finding = self._detect_join_date_filter_mirror_candidate(
            parsed,
            table_metadata,
            skills,
        )
        if mirrored_date_filter_finding is not None:
            findings.append(mirrored_date_filter_finding)
            rules_triggered.append(_RULE_JOIN_FILTER_BEFORE)

        stripped_order_sql = self._strip_order_by_in_subqueries(parsed, dialect=dialect)
        if stripped_order_sql is not None and stripped_order_sql != sql:
            revalidated = semantic_preflight_service.check(
                sql=stripped_order_sql,
                dialect=dialect,
                engine=connection.engine,
                context_tables=context_tables,
                metadata_table_columns=metadata_table_columns,
                requested_connector_name=selected_connector_name,
                selected_connector_name=selected_connector_name,
            )
            if revalidated.status == "pass":
                actions.append(
                    OptimizationAction(
                        action_id="strip_subquery_order_by",
                        kind="rewrite",
                        title="Remove non-semantic ORDER BY from subqueries",
                        summary="Subquery ORDER BY clauses without limiting semantics were removed.",
                        sql=stripped_order_sql,
                        applied=True,
                    )
                )
            else:
                warnings.append("sql_optimizer_subquery_order_reverted_preflight")

        alternatives_finding = self._detect_join_alternatives(parsed, table_metadata, skills)
        if alternatives_finding is not None:
            findings.append(alternatives_finding)
            rules_triggered.append(_RULE_JOIN_ALTERNATIVES)

        order_prefix_finding = self._detect_order_by_prefix_gap(parsed, table_metadata, skills)
        if order_prefix_finding is not None:
            findings.append(order_prefix_finding)
            rules_triggered.append(_RULE_ORDER_BY_PREFIX)

        skip_index_finding = self._detect_skip_index_candidate(
            parsed, table_metadata, explain_summary, skills
        )
        if skip_index_finding is not None:
            findings.append(skip_index_finding)
            rules_triggered.append(_RULE_SKIP_INDEX)

        mv_finding = self._detect_incremental_mv_candidate(parsed, skills)
        if mv_finding is not None:
            findings.append(mv_finding)
            rules_triggered.append(_RULE_INCREMENTAL_MV)

        executed_sql = sql
        if actions:
            preferred = next(
                (item for item in actions if item.action_id == "pushdown_predicates"), None
            )
            if preferred is None:
                preferred = next(
                    (item for item in actions if item.action_id == "strip_subquery_order_by"), None
                )
            if preferred is not None and preferred.sql:
                executed_sql = preferred.sql

        trace.optimizer_used = True
        trace.optimizer_provider = self._provider_for_engine(connection.engine)
        trace.optimized_sql_draft = executed_sql
        trace.optimized_sql_hash = self._sql_hash(executed_sql)
        trace.accepted_or_rejected = "accepted" if executed_sql != sql else "advisory_only"
        trace.applied = executed_sql != sql
        trace.risk_level = "low" if trace.applied else ("medium" if findings else "none")
        trace.confidence = 0.91 if trace.applied else (0.74 if findings else 0.55)
        trace.rules_triggered = list(dict.fromkeys(rules_triggered))
        trace.findings = findings
        trace.recommended_actions = actions
        trace.citations = self._collect_citations(findings=findings, actions=actions)
        trace.warnings = warnings
        trace.skills_used = [skill.name for skill in skills]
        if any(skill.content_bundle.assets for skill in skills):
            evidence_used.append("workspace_skill_bundle")
        trace.evidence_used = list(dict.fromkeys(evidence_used))
        return SqlOptimizationOutcome(sql_for_execution=executed_sql, trace=trace)

    def should_optimize(
        self,
        *,
        workspace_id: str,
        connection: EngineConnectionConfig,
        selected_connector_name: str,
    ) -> bool:
        _ = selected_connector_name
        if not self._supports_engine(connection.engine):
            return False
        return bool(
            self._match_skills(
                workspace_id=workspace_id,
                engine_scope=connection.engine,
            )
        )

    @staticmethod
    def _parse(*, sql: str, dialect: str) -> exp.Expression | None:
        try:
            return parse_one(sql, read=dialect)
        except ParseError:
            return None

    @staticmethod
    def _sql_hash(sql: str) -> str:
        return hashlib.sha256(sql.encode("utf-8")).hexdigest()

    @staticmethod
    def _supports_engine(engine: str) -> bool:
        return engine in {"clickhouse", "trino", "greenplum", "impala"}

    @staticmethod
    def _provider_for_engine(engine: str) -> str:
        if engine == "greenplum":
            return "greenplum_rule_advisor"
        if engine == "impala":
            return "impala_rule_advisor"
        if engine == "trino":
            return "trino_rule_advisor"
        return "clickhouse_rule_advisor"

    def provider_for_engine(self, engine: str) -> str:
        return self._provider_for_engine(engine)

    def _match_skills(
        self,
        *,
        workspace_id: str,
        engine_scope: str,
    ) -> list[WorkspaceSkillItem]:
        if self._workspace_skill_repository is None:
            return []
        return self._workspace_skill_repository.list_active_for_capability(
            workspace_id=workspace_id,
            capability_family="sql_optimization_advisor",
            engine_scope=engine_scope,
            limit=5,
        )

    def _inspect_clickhouse_tables(
        self,
        *,
        connection: EngineConnectionConfig,
        referenced_tables: list[str],
    ) -> ClickHouseInspectionResult:
        if not referenced_tables:
            return ClickHouseInspectionResult(tables=[], evidence_used=[])
        try:
            client = self._build_clickhouse_client(connection)
            results: list[TableOptimizationMetadata] = []
            evidence_used: set[str] = set()
            for reference in referenced_tables[:5]:
                database, table = self._extract_database_table(reference)
                if not database or not table:
                    continue
                ddl = self._fetch_show_create(client, database=database, table=table)
                metadata_row = self._fetch_system_table_row(client, database=database, table=table)
                columns = self._fetch_system_columns(client, database=database, table=table)
                if ddl:
                    evidence_used.add("ddl")
                if metadata_row:
                    evidence_used.add("system.tables")
                if columns:
                    evidence_used.add("system.columns")
                results.append(
                    TableOptimizationMetadata(
                        fqn=reference,
                        database=database,
                        table=table,
                        engine=metadata_row.get("engine") if metadata_row else None,
                        ddl=ddl
                        or (metadata_row.get("create_table_query") if metadata_row else None),
                        sorting_key=self._csv_parts(
                            metadata_row.get("sorting_key") if metadata_row else None
                        ),
                        primary_key=self._csv_parts(
                            metadata_row.get("primary_key") if metadata_row else None
                        ),
                        partition_key=(metadata_row.get("partition_key") if metadata_row else None)
                        or None,
                        skip_indexes=self._extract_skip_indexes(ddl or ""),
                        columns=columns,
                    )
                )
            return ClickHouseInspectionResult(
                tables=results,
                evidence_used=sorted(evidence_used),
            )
        except Exception:
            logger.exception("sql.optimizer.inspector_failed")
            return ClickHouseInspectionResult(tables=[], evidence_used=[])
        finally:
            if "client" in locals():
                client.close()

    @staticmethod
    def _fetch_show_create(client: Any, *, database: str, table: str) -> str | None:
        escaped_db = database.replace("`", "")
        escaped_table = table.replace("`", "")
        try:
            result = client.query(f"SHOW CREATE TABLE `{escaped_db}`.`{escaped_table}`")
        except Exception:
            return None
        for row in result.result_rows:
            if row and isinstance(row[0], str) and row[0].strip():
                return row[0]
        return None

    @staticmethod
    def _fetch_system_table_row(client: Any, *, database: str, table: str) -> dict[str, str]:
        escaped_db = database.replace("'", "''")
        escaped_table = table.replace("'", "''")
        result = client.query(
            "SELECT engine, partition_key, sorting_key, primary_key, create_table_query "
            f"FROM system.tables WHERE database = '{escaped_db}' AND name = '{escaped_table}' LIMIT 1"
        )
        if not result.result_rows:
            return {}
        row = result.result_rows[0]
        columns = ["engine", "partition_key", "sorting_key", "primary_key", "create_table_query"]
        return {columns[index]: str(value or "") for index, value in enumerate(row[: len(columns)])}

    @staticmethod
    def _fetch_system_columns(client: Any, *, database: str, table: str) -> dict[str, str]:
        escaped_db = database.replace("'", "''")
        escaped_table = table.replace("'", "''")
        result = client.query(
            "SELECT name, type FROM system.columns "
            f"WHERE database = '{escaped_db}' AND table = '{escaped_table}' ORDER BY position LIMIT 500"
        )
        return {
            str(row[0]).strip().lower(): str(row[1]).strip()
            for row in result.result_rows
            if len(row) >= 2 and str(row[0]).strip()
        }

    @staticmethod
    def _extract_database_table(reference: str) -> tuple[str | None, str | None]:
        parts = [part.strip() for part in reference.split(".") if part.strip()]
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        return None, None

    @staticmethod
    def _csv_parts(raw_value: str | None) -> list[str]:
        if not raw_value:
            return []
        clean = raw_value.strip().strip("()")
        return [part.strip().strip("`").lower() for part in clean.split(",") if part.strip()]

    @staticmethod
    def _extract_skip_indexes(ddl: str) -> list[str]:
        return re.findall(r"INDEX\s+([a-zA-Z0-9_]+)\s+", ddl, flags=re.IGNORECASE)

    @staticmethod
    def _explain_indexes(connection: EngineConnectionConfig, *, sql: str) -> str | None:
        try:
            client = SqlOptimizationService._build_clickhouse_client(connection)
            result = client.query(f"EXPLAIN indexes = 1 {sql}")
            lines = [
                str(row[0]).strip() for row in result.result_rows if row and str(row[0]).strip()
            ]
            return "\n".join(lines[:40]) or None
        except Exception:
            return None
        finally:
            if "client" in locals():
                client.close()

    @staticmethod
    def _build_clickhouse_client(connection: EngineConnectionConfig) -> Any:
        return clickhouse_connect.get_client(
            host=connection.host,
            port=connection.port,
            username=connection.username,
            password=connection.password,
            database=connection.database,
            secure=connection.secure,
            verify=connection.verify,
            connect_timeout=_OPTIMIZER_CLICKHOUSE_CONNECT_TIMEOUT,
            send_receive_timeout=_OPTIMIZER_CLICKHOUSE_SEND_RECEIVE_TIMEOUT,
            query_retries=_OPTIMIZER_CLICKHOUSE_QUERY_RETRIES,
        )

    @staticmethod
    def _requires_clickhouse_table_inspection(parsed: exp.Expression) -> bool:
        return any((
            parsed.find(exp.Join) is not None,
            parsed.find(exp.Order) is not None,
        ))

    @staticmethod
    def _requires_clickhouse_explain(parsed: exp.Expression) -> bool:
        return parsed.find(exp.Join) is not None

    @staticmethod
    def _normalize_identifier(value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().strip("`").lower()
        return normalized or None

    @classmethod
    def _resolve_table_alias_metadata(
        cls,
        parsed: exp.Expression,
        table_metadata: list[TableOptimizationMetadata],
    ) -> dict[str, TableOptimizationMetadata]:
        select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
        if not isinstance(select, exp.Select):
            return {}

        metadata_by_fqn = {
            (item.database.lower(), item.table.lower()): item for item in table_metadata
        }
        metadata_by_table: dict[str, list[TableOptimizationMetadata]] = {}
        for item in table_metadata:
            metadata_by_table.setdefault(item.table.lower(), []).append(item)

        def resolve_source(
            source: exp.Expression | None,
        ) -> tuple[str, TableOptimizationMetadata] | None:
            if not isinstance(source, exp.Table):
                return None
            alias = cls._normalize_identifier(source.alias_or_name)
            table_name = cls._normalize_identifier(source.name)
            database_name = cls._normalize_identifier(source.db)
            if alias is None or table_name is None:
                return None
            metadata = None
            if database_name is not None:
                metadata = metadata_by_fqn.get((database_name, table_name))
            if metadata is None:
                candidates = metadata_by_table.get(table_name, [])
                if len(candidates) == 1:
                    metadata = candidates[0]
            if metadata is None:
                return None
            return (alias, metadata)

        alias_metadata: dict[str, TableOptimizationMetadata] = {}
        from_exp = select.args.get("from")
        if isinstance(from_exp, exp.From):
            resolved = resolve_source(from_exp.this)
            if resolved is not None:
                alias_metadata[resolved[0]] = resolved[1]
        for join in select.args.get("joins") or []:
            resolved = resolve_source(join.this)
            if resolved is not None:
                alias_metadata[resolved[0]] = resolved[1]
        return alias_metadata

    @classmethod
    def _collect_where_columns_by_alias(
        cls,
        parsed: exp.Expression,
        table_metadata: list[TableOptimizationMetadata],
    ) -> dict[str, set[str]]:
        alias_metadata = cls._resolve_table_alias_metadata(parsed, table_metadata)
        filters_by_alias: dict[str, set[str]] = {alias: set() for alias in alias_metadata}
        unqualified_columns: list[str] = []

        for where in parsed.find_all(exp.Where):
            for column in where.find_all(exp.Column):
                column_name = cls._normalize_identifier(column.name)
                if column_name is None:
                    continue
                table_alias = cls._normalize_identifier(column.table)
                if table_alias is None:
                    unqualified_columns.append(column_name)
                    continue
                filters_by_alias.setdefault(table_alias, set()).add(column_name)

        if len(alias_metadata) == 1:
            only_alias = next(iter(alias_metadata))
            filters_by_alias.setdefault(only_alias, set()).update(unqualified_columns)
            return filters_by_alias

        for column_name in unqualified_columns:
            matches = [
                alias
                for alias, metadata in alias_metadata.items()
                if column_name in metadata.columns
            ]
            if len(matches) == 1:
                filters_by_alias.setdefault(matches[0], set()).add(column_name)
        return filters_by_alias

    @staticmethod
    def _is_date_like_type(column_type: str | None) -> bool:
        if not column_type:
            return False
        normalized = column_type.strip().lower()
        return "date" in normalized or "datetime" in normalized

    @staticmethod
    def _looks_like_date_column_name(column_name: str) -> bool:
        normalized = column_name.lower()
        markers = ("date", "_dt", "dttm", "datetime", "_at")
        return any(marker in normalized for marker in markers)

    @classmethod
    def _is_date_filter_column(
        cls,
        column_name: str,
        metadata: TableOptimizationMetadata | None,
    ) -> bool:
        if metadata is not None and cls._is_date_like_type(metadata.columns.get(column_name)):
            return True
        return cls._looks_like_date_column_name(column_name)

    def _analyze_join_filter_pushdown(
        self,
        parsed: exp.Expression,
        *,
        dialect: str,
    ) -> tuple[OptimizationFinding | None, str | None]:
        select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
        if not isinstance(select, exp.Select):
            return None, None
        joins = list(select.args.get("joins") or [])
        outer_where = select.args.get("where")
        if not joins or outer_where is None:
            return None, None
        if not self._is_safe_for_join_pushdown(select):
            return None, None

        alias_sources = self._select_alias_sources(select)
        predicates = self._split_conjunctions(outer_where.this)
        moved_aliases: set[str] = set()
        remaining: list[exp.Expression] = []
        updated_sources = {alias: source.copy() for alias, source in alias_sources.items()}

        for predicate in predicates:
            aliases = self._predicate_aliases(predicate)
            if len(aliases) != 1:
                remaining.append(predicate)
                continue
            alias = next(iter(aliases))
            source = updated_sources.get(alias)
            if source is None:
                remaining.append(predicate)
                continue
            updated_sources[alias] = self._wrap_source_with_filter(
                source=source, predicate=predicate, alias=alias
            )
            moved_aliases.add(alias)

        if not moved_aliases:
            return None, None

        rewritten = parsed.copy()
        rewritten_select = (
            rewritten if isinstance(rewritten, exp.Select) else rewritten.find(exp.Select)
        )
        if not isinstance(rewritten_select, exp.Select):
            return None, None
        rewritten_sources = self._select_alias_sources(rewritten_select)
        for alias, source in updated_sources.items():
            target = rewritten_sources.get(alias)
            if target is None:
                continue
            target.replace(source)
        if remaining:
            rewritten_select.set("where", exp.Where(this=self._join_conjunctions(remaining)))
        else:
            rewritten_select.set("where", None)

        finding = OptimizationFinding(
            rule_id=_RULE_JOIN_FILTER_BEFORE,
            title="Filter can be pushed below INNER JOIN",
            summary=(
                "Single-table predicates were found in the outer WHERE clause and can be moved "
                "closer to source scans."
            ),
            severity="info",
            applies_to=sorted(moved_aliases),
        )
        return finding, self._sql_formatter.format_expression(rewritten, dialect=dialect)

    @staticmethod
    def _is_safe_for_join_pushdown(select: exp.Select) -> bool:
        if select.args.get("group") is not None:
            return False
        if select.args.get("having") is not None:
            return False
        if select.args.get("distinct") is not None:
            return False
        if select.args.get("limit") is not None:
            return False
        if select.find(exp.Window) is not None:
            return False
        for join in select.args.get("joins") or []:
            kind = str(join.args.get("kind") or "").upper()
            side = str(join.args.get("side") or "").upper()
            if (kind and kind != "INNER") or side:
                return False
        return True

    @staticmethod
    def _select_alias_sources(select: exp.Select) -> dict[str, exp.Expression]:
        sources: dict[str, exp.Expression] = {}
        from_exp = select.args.get("from")
        if isinstance(from_exp, exp.From) and from_exp.this is not None:
            source = from_exp.this
            alias = source.alias_or_name
            if alias:
                sources[alias] = source
        for join in select.args.get("joins") or []:
            source = join.this
            alias = source.alias_or_name
            if alias:
                sources[alias] = source
        return sources

    @staticmethod
    def _split_conjunctions(expression: exp.Expression) -> list[exp.Expression]:
        if isinstance(expression, exp.And):
            return SqlOptimizationService._split_conjunctions(
                expression.left
            ) + SqlOptimizationService._split_conjunctions(expression.right)
        return [expression]

    @staticmethod
    def _join_conjunctions(expressions: list[exp.Expression]) -> exp.Expression:
        current = expressions[0].copy()
        for expression in expressions[1:]:
            current = exp.and_(current, expression.copy())
        return current

    @staticmethod
    def _predicate_aliases(predicate: exp.Expression) -> set[str]:
        aliases: set[str] = set()
        for column in predicate.find_all(exp.Column):
            if column.table:
                aliases.add(str(column.table))
        return aliases

    @staticmethod
    def _wrap_source_with_filter(
        *,
        source: exp.Expression,
        predicate: exp.Expression,
        alias: str,
    ) -> exp.Expression:
        source_copy = source.copy()
        if isinstance(source_copy, exp.Subquery) and isinstance(source_copy.this, exp.Select):
            inner_select = source_copy.this.copy()
            if inner_select.args.get("where") is not None:
                combined = exp.and_(inner_select.args["where"].this.copy(), predicate.copy())
                inner_select.set("where", exp.Where(this=combined))
            else:
                inner_select.set("where", exp.Where(this=predicate.copy()))
            source_copy.set("this", inner_select)
            return source_copy

        alias_expression = source_copy.args.get("alias")
        bare_source = source_copy.copy()
        bare_source.set("alias", None)
        wrapped = exp.select("*").from_(bare_source).where(predicate.copy()).subquery(alias=alias)
        if alias_expression is not None:
            wrapped.set("alias", alias_expression.copy())
        return wrapped

    def _strip_order_by_in_subqueries(self, parsed: exp.Expression, *, dialect: str) -> str | None:
        rewritten = parsed.copy()
        changed = False
        for subquery in rewritten.find_all(exp.Subquery):
            inner = subquery.this
            if not isinstance(inner, exp.Select):
                continue
            if inner.args.get("order") is None:
                continue
            if any(
                inner.args.get(key) is not None for key in ("limit", "offset", "distinct", "sample")
            ):
                continue
            if inner.find(exp.Window) is not None:
                continue
            if inner.parent and isinstance(inner.parent, exp.Union | exp.Intersect | exp.Except):
                continue
            inner.set("order", None)
            changed = True
        if not changed:
            return None
        return self._sql_formatter.format_expression(rewritten, dialect=dialect)

    def _detect_join_alternatives(
        self,
        parsed: exp.Expression,
        table_metadata: list[TableOptimizationMetadata],
        skills: list[WorkspaceSkillItem],
    ) -> OptimizationFinding | None:
        joins = list(parsed.find_all(exp.Join))
        if not joins:
            return None
        if len(joins) < 2 and not any("dim" in item.table.lower() for item in table_metadata):
            return None
        return OptimizationFinding(
            rule_id=_RULE_JOIN_ALTERNATIVES,
            title="Consider dictionary or denormalized serving path",
            summary=(
                "The query joins dimension-style data at read time. Repeated analytical patterns like this "
                "often benefit from dictionaries or denormalized serving tables."
            ),
            severity="warning",
            citations=self._rule_citations(_RULE_JOIN_ALTERNATIVES, skills),
        )

    def _detect_join_date_filter_mirror_candidate(
        self,
        parsed: exp.Expression,
        table_metadata: list[TableOptimizationMetadata],
        skills: list[WorkspaceSkillItem],
    ) -> OptimizationFinding | None:
        select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
        if not isinstance(select, exp.Select):
            return None

        alias_metadata = self._resolve_table_alias_metadata(parsed, table_metadata)
        if len(alias_metadata) < 2:
            return None

        date_filters_by_alias: dict[str, set[str]] = {}
        for where in parsed.find_all(exp.Where):
            for predicate in self._split_conjunctions(where.this):
                aliases = {alias.lower() for alias in self._predicate_aliases(predicate)}
                if len(aliases) != 1:
                    continue
                alias = next(iter(aliases))
                metadata = alias_metadata.get(alias)
                if metadata is None:
                    continue
                date_columns = {
                    column.name.lower()
                    for column in predicate.find_all(exp.Column)
                    if column.name
                    and self._normalize_identifier(column.table) == alias
                    and self._is_date_filter_column(column.name.lower(), metadata)
                }
                if date_columns:
                    date_filters_by_alias.setdefault(alias, set()).update(date_columns)

        if not date_filters_by_alias:
            return None

        join_pairs: set[tuple[str, str]] = set()
        for join in select.args.get("joins") or []:
            on_expression = join.args.get("on")
            if on_expression is None:
                continue
            for equality in on_expression.find_all(exp.EQ):
                left_aliases = {alias.lower() for alias in self._predicate_aliases(equality.left)}
                right_aliases = {alias.lower() for alias in self._predicate_aliases(equality.right)}
                if len(left_aliases) != 1 or len(right_aliases) != 1:
                    continue
                left_alias = next(iter(left_aliases))
                right_alias = next(iter(right_aliases))
                if left_alias == right_alias:
                    continue
                join_pairs.add(tuple(sorted((left_alias, right_alias))))

        for left_alias, right_alias in sorted(join_pairs):
            left_metadata = alias_metadata.get(left_alias)
            right_metadata = alias_metadata.get(right_alias)
            if left_metadata is None or right_metadata is None:
                continue
            left_columns = date_filters_by_alias.get(left_alias, set())
            right_columns = date_filters_by_alias.get(right_alias, set())
            if left_columns and not right_columns:
                mirrored_columns = [
                    column_name
                    for column_name in sorted(left_columns)
                    if self._is_date_filter_column(column_name, right_metadata)
                ]
                if mirrored_columns:
                    return OptimizationFinding(
                        rule_id=_RULE_JOIN_FILTER_BEFORE,
                        title="Date filter can be mirrored before the JOIN",
                        summary=(
                            f"Query filters `{left_alias}.{mirrored_columns[0]}` but leaves "
                            f"`{right_alias}.{mirrored_columns[0]}` unfiltered. If both columns represent "
                            "the same business date, filter both inputs before the INNER JOIN to shrink scans."
                        ),
                        severity="warning",
                        applies_to=[left_metadata.fqn, right_metadata.fqn],
                        citations=self._rule_citations(_RULE_JOIN_FILTER_BEFORE, skills),
                    )
            if right_columns and not left_columns:
                mirrored_columns = [
                    column_name
                    for column_name in sorted(right_columns)
                    if self._is_date_filter_column(column_name, left_metadata)
                ]
                if mirrored_columns:
                    return OptimizationFinding(
                        rule_id=_RULE_JOIN_FILTER_BEFORE,
                        title="Date filter can be mirrored before the JOIN",
                        summary=(
                            f"Query filters `{right_alias}.{mirrored_columns[0]}` but leaves "
                            f"`{left_alias}.{mirrored_columns[0]}` unfiltered. If both columns represent "
                            "the same business date, filter both inputs before the INNER JOIN to shrink scans."
                        ),
                        severity="warning",
                        applies_to=[left_metadata.fqn, right_metadata.fqn],
                        citations=self._rule_citations(_RULE_JOIN_FILTER_BEFORE, skills),
                    )
        return None

    def _detect_order_by_prefix_gap(
        self,
        parsed: exp.Expression,
        table_metadata: list[TableOptimizationMetadata],
        skills: list[WorkspaceSkillItem],
    ) -> OptimizationFinding | None:
        filters_by_alias = self._collect_where_columns_by_alias(parsed, table_metadata)
        alias_metadata = self._resolve_table_alias_metadata(parsed, table_metadata)
        for alias, metadata in alias_metadata.items():
            if not metadata.sorting_key:
                continue
            where_columns = filters_by_alias.get(alias, set())
            if not where_columns:
                continue
            prefix = metadata.sorting_key[0]
            if prefix and prefix not in where_columns:
                return OptimizationFinding(
                    rule_id=_RULE_ORDER_BY_PREFIX,
                    title="Filter does not use ORDER BY prefix",
                    summary=(
                        f"Table `{metadata.database}.{metadata.table}` sorts by `{prefix}` first, but the query "
                        "filters only on later or unrelated columns."
                    ),
                    severity="warning",
                    applies_to=[metadata.fqn],
                    citations=self._rule_citations(_RULE_ORDER_BY_PREFIX, skills),
                )
        return None

    def _detect_skip_index_candidate(
        self,
        parsed: exp.Expression,
        table_metadata: list[TableOptimizationMetadata],
        explain_summary: str | None,
        skills: list[WorkspaceSkillItem],
    ) -> OptimizationFinding | None:
        filters_by_alias = self._collect_where_columns_by_alias(parsed, table_metadata)
        alias_metadata = self._resolve_table_alias_metadata(parsed, table_metadata)
        for alias, metadata in alias_metadata.items():
            where_columns = filters_by_alias.get(alias, set())
            if not where_columns:
                continue
            non_order_filters = [
                column for column in where_columns if column not in metadata.sorting_key
            ]
            if not non_order_filters or metadata.skip_indexes:
                continue
            summary_suffix = " EXPLAIN suggests index pruning is weak." if explain_summary else ""
            return OptimizationFinding(
                rule_id=_RULE_SKIP_INDEX,
                title="Non-ORDER BY filters may need skipping index",
                summary=(
                    f"Query filters on `{', '.join(sorted(non_order_filters)[:3])}` which are outside the sorting key.{summary_suffix}"
                ),
                severity="warning",
                applies_to=[metadata.fqn],
                citations=self._rule_citations(_RULE_SKIP_INDEX, skills),
            )
        return None

    def _detect_incremental_mv_candidate(
        self,
        parsed: exp.Expression,
        skills: list[WorkspaceSkillItem],
    ) -> OptimizationFinding | None:
        select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
        if not isinstance(select, exp.Select):
            return None
        if select.args.get("group") is None:
            return None
        if (
            select.find(exp.Count) is None
            and select.find(exp.Sum) is None
            and select.find(exp.Avg) is None
        ):
            return None
        time_bucket_functions = {
            "tostartofday",
            "tostartofhour",
            "todate",
            "todate32",
            "date_trunc",
        }
        if not any(
            isinstance(function, exp.Func) and function.sql_name().lower() in time_bucket_functions
            for function in select.find_all(exp.Func)
        ):
            return None
        return OptimizationFinding(
            rule_id=_RULE_INCREMENTAL_MV,
            title="Repeated time-bucket aggregation fits incremental MV",
            summary=(
                "This query does grouped time-series aggregation. If it becomes a repeated workload, "
                "an incremental materialized view is a better serving strategy than re-scanning raw facts."
            ),
            severity="warning",
            citations=self._rule_citations(_RULE_INCREMENTAL_MV, skills),
        )

    @staticmethod
    def _rule_citations(
        rule_id: str, skills: list[WorkspaceSkillItem]
    ) -> list[OptimizationCitation]:
        citations = [
            OptimizationCitation(
                kind="rule",
                label=rule_id,
                reference=rule_id,
            )
        ]
        for skill in skills[:2]:
            rule_asset = find_rule_asset(skill, rule_id)
            citations.append(
                OptimizationCitation(
                    kind="workspace_skill_rule" if rule_asset is not None else "workspace_skill",
                    label=(
                        f"{skill.name}:{rule_asset.rule_id}"
                        if rule_asset is not None and rule_asset.rule_id
                        else skill.name
                    ),
                    reference=(
                        rule_asset.source_url if rule_asset is not None else skill.source_url
                    ),
                )
            )
        return citations

    @staticmethod
    def _collect_citations(
        *,
        findings: list[OptimizationFinding],
        actions: list[OptimizationAction],
    ) -> list[OptimizationCitation]:
        collected: list[OptimizationCitation] = []
        seen: set[tuple[str, str]] = set()
        for source in [*findings, *actions]:
            for citation in source.citations:
                key = (citation.kind, citation.reference)
                if key in seen:
                    continue
                seen.add(key)
                collected.append(citation)
        return collected
