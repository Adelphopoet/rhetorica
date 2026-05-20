"""Semantic SQL preflight checks before actual execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Literal

from pydantic import BaseModel, Field
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import Scope, build_scope

PreflightStatus = Literal["pass", "fail", "uncertain"]

_UNSUPPORTED_FUNCTIONS: dict[str, set[str]] = {
    "clickhouse": {"unnest", "generate_series"},
    "trino": {"arrayjoin", "uniqexact", "todatetime"},
}

_UNSUPPORTED_SQL_PATTERNS: dict[str, tuple[tuple[str, str], ...]] = {
    "trino": (
        (r"\bsystem\.tables\b", "Table 'system.tables' is ClickHouse-specific and not valid for Trino."),
        (r"\bsystem\.columns\b", "Table 'system.columns' is ClickHouse-specific and not valid for Trino."),
        (r"\bprewhere\b", "Clause 'PREWHERE' is ClickHouse-specific and not valid for Trino."),
        (r"\blimit\s+by\b", "Clause 'LIMIT BY' is ClickHouse-specific and not valid for Trino."),
        (r"\btodate\s*\(", "Function 'toDate' is ClickHouse-specific and not valid for Trino."),
        (r"\btodatetime\s*\(", "Function 'toDateTime' is ClickHouse-specific and not valid for Trino."),
        (r"\buniqexact\s*\(", "Function 'uniqExact' is ClickHouse-specific and not valid for Trino."),
        (r"\barrayjoin\s*\(", "Function 'arrayJoin' is ClickHouse-specific and not valid for Trino."),
    ),
}


@dataclass(frozen=True)
class _PhysicalTableReference:
    """Resolved physical table reference from a SQL scope tree."""

    identifier: str
    alias: str | None = None
    db: str | None = None
    catalog: str | None = None


@dataclass(frozen=True)
class _SqlParseOutcome:
    """SQL parser output with deterministic failure diagnostics."""

    expression: exp.Expression | None
    error: str | None = None


class SqlPreflightError(BaseModel):
    """Structured preflight error item."""

    code: str
    message: str
    identifier: str | None = None
    table: str | None = None
    suggestions: list[str] = Field(default_factory=list)


class SqlPreflightResult(BaseModel):
    """Result payload for SQL semantic preflight."""

    status: PreflightStatus
    errors: list[SqlPreflightError] = Field(default_factory=list)
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_columns: list[str] = Field(default_factory=list)


class SqlStatementInspection(BaseModel):
    """Parsed SQL inspection result used before semantic preflight."""

    parsed: bool
    parse_error: str | None = None
    is_introspection: bool = False
    schema_probe_table: str | None = None
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_columns: list[str] = Field(default_factory=list)


class SemanticSqlPreflightService:
    """Checks semantic SQL validity against available metadata context."""

    def inspect(
        self,
        *,
        sql: str,
        dialect: str,
    ) -> SqlStatementInspection:
        parse_outcome = self._parse(sql=sql, dialect=dialect)
        is_introspection = self._is_introspection_sql(sql)
        parsed = parse_outcome.expression
        if parsed is None:
            return SqlStatementInspection(
                parsed=False,
                parse_error=parse_outcome.error,
                is_introspection=is_introspection,
            )

        scope_root = self._build_scope(parsed)
        table_references = self._extract_table_references(
            parsed=parsed,
            scope_root=scope_root,
        )
        return SqlStatementInspection(
            parsed=True,
            is_introspection=is_introspection,
            schema_probe_table=self._extract_schema_probe_table_name(parsed),
            referenced_tables=[reference.identifier for reference in table_references],
            referenced_columns=self._extract_columns(parsed),
        )

    def check(
        self,
        *,
        sql: str,
        dialect: str,
        engine: str,
        context_tables: list[str],
        metadata_table_columns: dict[str, set[str]],
        requested_connector_name: str | None,
        selected_connector_name: str | None,
    ) -> SqlPreflightResult:
        errors: list[SqlPreflightError] = []
        errors.extend(self._detect_unsupported_sql_patterns(sql=sql, engine=engine))

        inspection = self.inspect(sql=sql, dialect=dialect)
        if not inspection.parsed:
            if inspection.is_introspection:
                return SqlPreflightResult(status="fail" if errors else "pass", errors=errors)
            return SqlPreflightResult(
                status="fail",
                errors=errors
                or [
                    SqlPreflightError(
                        code="sql_parse_failed",
                        message=self._parse_error_message(inspection.parse_error),
                    )
                ],
            )

        parsed = self._parse(sql=sql, dialect=dialect).expression
        if parsed is None:
            return SqlPreflightResult(
                status="fail",
                errors=[
                    SqlPreflightError(
                        code="sql_parse_failed",
                        message="SQL parsing failed after successful inspection pass.",
                    )
                ],
            )
        scope_root = self._build_scope(parsed)
        table_references = self._extract_table_references(
            parsed=parsed,
            scope_root=scope_root,
        )
        referenced_tables = list(inspection.referenced_tables)
        referenced_columns = list(inspection.referenced_columns)
        if inspection.is_introspection:
            return SqlPreflightResult(
                status="fail" if errors else "pass",
                errors=errors,
                referenced_tables=referenced_tables,
                referenced_columns=referenced_columns,
            )
        builtin_tables = {
            self._normalize_identifier(table)
            for table in referenced_tables
            if self._is_builtin_table(table)
        }
        references_only_builtin_tables = bool(referenced_tables) and (
            len(builtin_tables) == len(referenced_tables)
        )

        if (
            not context_tables
            and not metadata_table_columns
            and not references_only_builtin_tables
        ):
            return SqlPreflightResult(
                status="uncertain",
                errors=[
                    SqlPreflightError(
                        code="metadata_context_missing",
                        message="Metadata context is empty; cannot verify table/column references.",
                    )
                ],
                referenced_tables=referenced_tables,
                referenced_columns=referenced_columns,
            )

        normalized_known_tables = {
            self._normalize_identifier(table): table
            for table in context_tables
            if table and table.strip()
        }
        if not normalized_known_tables:
            normalized_known_tables = {
                self._normalize_identifier(table): table
                for table in metadata_table_columns
                if table and table.strip()
            }

        resolved_tables: dict[str, str] = {}
        for reference in table_references:
            if self._is_builtin_table(reference.identifier):
                continue
            resolved_table = self._resolve_table_fqn(
                reference.identifier,
                normalized_known_tables,
            )
            if resolved_table is None:
                errors.append(
                    SqlPreflightError(
                        code="missing_table",
                        message=(
                            f"Table '{reference.identifier}' is missing in metadata context."
                        ),
                        identifier=reference.identifier,
                    )
                )
                continue
            resolved_tables[self._normalize_identifier(reference.identifier)] = (
                resolved_table
            )

        cross_database_error = self._cross_database_reference_error(
            resolved_tables=resolved_tables.values(),
        )
        if cross_database_error is not None:
            errors.append(cross_database_error)

        normalized_columns = {
            self._normalize_identifier(table): {
                self._normalize_identifier(column)
                for column in columns
                if column and column.strip()
            }
            for table, columns in metadata_table_columns.items()
        }

        if normalized_columns:
            errors.extend(
                self._validate_columns(
                    parsed=parsed,
                    scope_root=scope_root,
                    normalized_known_tables=normalized_known_tables,
                    normalized_columns=normalized_columns,
                    references_only_builtin_tables=references_only_builtin_tables,
                )
            )

        if (
            requested_connector_name
            and selected_connector_name
            and requested_connector_name.strip().lower()
            != selected_connector_name.strip().lower()
        ):
            errors.append(
                SqlPreflightError(
                    code="connector_mismatch",
                    message=(
                        "Requested connector does not match selected connector: "
                        f"'{requested_connector_name}' vs '{selected_connector_name}'."
                    ),
                )
            )

        unsupported = _UNSUPPORTED_FUNCTIONS.get(engine.lower(), set())
        if unsupported:
            for function in parsed.find_all(exp.Func):
                fn_name = self._normalize_identifier(function.sql_name() or "")
                if fn_name in unsupported:
                    errors.append(
                        SqlPreflightError(
                            code="unsupported_function_for_engine",
                            message=(
                                f"Function '{function.sql_name()}' is not supported "
                                f"for engine '{engine}'."
                            ),
                        )
                    )

        status: PreflightStatus = "fail" if errors else "pass"
        return SqlPreflightResult(
            status=status,
            errors=errors,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
        )

    @staticmethod
    def _detect_unsupported_sql_patterns(
        *,
        sql: str,
        engine: str,
    ) -> list[SqlPreflightError]:
        normalized_engine = engine.strip().lower()
        patterns = _UNSUPPORTED_SQL_PATTERNS.get(normalized_engine, ())
        lowered_sql = sql.lower()
        errors: list[SqlPreflightError] = []
        for pattern, message in patterns:
            if re.search(pattern, lowered_sql):
                errors.append(
                    SqlPreflightError(
                        code="unsupported_syntax_for_engine",
                        message=message,
                    )
                )
        return errors

    @staticmethod
    def _parse(sql: str, dialect: str) -> _SqlParseOutcome:
        try:
            return _SqlParseOutcome(expression=parse_one(sql, read=dialect))
        except ParseError as exc:
            return _SqlParseOutcome(expression=None, error=str(exc))

    @staticmethod
    def _parse_error_message(parse_error: str | None) -> str:
        if not parse_error:
            return "Unable to parse SQL for semantic preflight."
        return f"Unable to parse SQL for semantic preflight: {parse_error}"

    @staticmethod
    def _build_scope(parsed: exp.Expression) -> Scope | None:
        try:
            return build_scope(parsed)
        except Exception:
            return None

    @classmethod
    def _extract_table_references(
        cls,
        *,
        parsed: exp.Expression,
        scope_root: Scope | None,
    ) -> list[_PhysicalTableReference]:
        if scope_root is None:
            return cls._extract_table_references_without_scope(parsed)

        references: list[_PhysicalTableReference] = []
        seen: set[str] = set()
        for scope in scope_root.traverse():
            for alias, (_node, source) in scope.selected_sources.items():
                if not isinstance(source, exp.Table):
                    continue
                identifier = cls._table_identifier(source)
                normalized_identifier = cls._normalize_identifier(identifier)
                if not normalized_identifier or normalized_identifier in seen:
                    continue
                seen.add(normalized_identifier)
                references.append(
                    _PhysicalTableReference(
                        identifier=identifier,
                        alias=alias if isinstance(alias, str) and alias.strip() else None,
                        db=cls._optional_identifier(source.db),
                        catalog=cls._optional_identifier(source.catalog),
                    )
                )
        return references

    @classmethod
    def _extract_table_references_without_scope(
        cls,
        parsed: exp.Expression,
    ) -> list[_PhysicalTableReference]:
        references: list[_PhysicalTableReference] = []
        seen: set[str] = set()
        for table in parsed.find_all(exp.Table):
            identifier = cls._table_identifier(table)
            normalized_identifier = cls._normalize_identifier(identifier)
            if not normalized_identifier or normalized_identifier in seen:
                continue
            seen.add(normalized_identifier)
            references.append(_PhysicalTableReference(identifier=identifier))
        return references

    @staticmethod
    def _extract_columns(parsed: exp.Expression) -> list[str]:
        columns: list[str] = []
        seen: set[str] = set()
        for column in parsed.find_all(exp.Column):
            full_name = column.sql(dialect=None, identify=False, normalize=False)
            normalized = SemanticSqlPreflightService._normalize_identifier(full_name)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            columns.append(full_name)
        return columns

    @staticmethod
    def _suggest_columns(
        *,
        normalized_column: str,
        available_columns: set[str],
    ) -> list[str]:
        if not normalized_column or not available_columns:
            return []
        return get_close_matches(
            normalized_column,
            sorted(available_columns),
            n=3,
            cutoff=0.6,
        )

    @staticmethod
    def _format_suggestion_suffix(suggestions: list[str]) -> str:
        if not suggestions:
            return ""
        return " Similar columns: " + ", ".join(suggestions) + "."

    @staticmethod
    def _resolve_table_fqn(
        table: str,
        known_tables: dict[str, str],
    ) -> str | None:
        normalized = SemanticSqlPreflightService._normalize_identifier(table)
        if normalized in known_tables:
            return known_tables[normalized]
        target_parts = [part for part in normalized.split(".") if part]
        best_match: str | None = None
        best_width = 0
        ambiguous = False
        for known_normalized, known_original in known_tables.items():
            known_parts = [part for part in known_normalized.split(".") if part]
            max_width = min(len(target_parts), len(known_parts))
            for width in range(max_width, 0, -1):
                if known_parts[-width:] != target_parts[-width:]:
                    continue
                if width > best_width:
                    best_match = known_original
                    best_width = width
                    ambiguous = False
                elif width == best_width and best_match != known_original:
                    ambiguous = True
                break
        if best_match is not None and not ambiguous:
            return best_match
        return None

    @staticmethod
    def _normalize_identifier(value: str) -> str:
        return value.strip().replace("`", "").replace('"', "").lower()

    @classmethod
    def resolve_table_reference(
        cls,
        table: str,
        known_tables: list[str] | dict[str, str],
    ) -> str | None:
        if isinstance(known_tables, dict):
            normalized_known_tables = {
                cls._normalize_identifier(key): value
                for key, value in known_tables.items()
                if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip()
            }
            return cls._resolve_table_fqn(table, normalized_known_tables)
        normalized_known_tables = {
            cls._normalize_identifier(candidate): candidate
            for candidate in known_tables
            if isinstance(candidate, str) and candidate.strip()
        }
        return cls._resolve_table_fqn(table, normalized_known_tables)

    @classmethod
    def is_builtin_table_reference(cls, table: str) -> bool:
        return cls._is_builtin_table(table)

    @classmethod
    def _column_scope_candidates(
        cls,
        *,
        table: str,
        normalized_columns: dict[str, set[str]],
    ) -> list[str]:
        normalized = cls._normalize_identifier(table)
        if not normalized:
            return []
        if normalized in normalized_columns:
            return [normalized]

        parts = [part for part in normalized.split(".") if part]
        for width in range(len(parts), 0, -1):
            suffix = ".".join(parts[-width:])
            matches = [
                table_key
                for table_key in normalized_columns
                if table_key == suffix or table_key.endswith(f".{suffix}")
            ]
            if len(matches) == 1:
                return matches
        return []

    @staticmethod
    def _available_columns_for_scopes(
        scope_keys: list[str],
        normalized_columns: dict[str, set[str]],
    ) -> set[str]:
        available: set[str] = set()
        for scope_key in scope_keys:
            available.update(normalized_columns.get(scope_key, set()))
        return available

    @staticmethod
    def _extract_schema_probe_table_name(parsed: exp.Expression) -> str | None:
        select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
        if not isinstance(select, exp.Select):
            return None
        if list(select.find_all(exp.Join)):
            return None
        from_exp = select.args.get("from")
        if not isinstance(from_exp, exp.From) or not isinstance(from_exp.this, exp.Table):
            return None
        source_table = from_exp.this
        for projection in select.expressions:
            if isinstance(projection, exp.Star):
                continue
            if isinstance(projection, exp.Column):
                continue
            if isinstance(projection, exp.Alias) and isinstance(projection.this, exp.Column):
                continue
            return None
        parts = [
            part.strip("`\"")
            for part in (source_table.catalog, source_table.db, source_table.name)
            if isinstance(part, str) and part.strip()
        ]
        if not parts:
            return None
        return ".".join(parts)

    @classmethod
    def _validate_columns(
        cls,
        *,
        parsed: exp.Expression,
        scope_root: Scope | None,
        normalized_known_tables: dict[str, str],
        normalized_columns: dict[str, set[str]],
        references_only_builtin_tables: bool,
    ) -> list[SqlPreflightError]:
        if scope_root is None:
            return cls._validate_columns_without_scope(
                parsed=parsed,
                normalized_known_tables=normalized_known_tables,
                normalized_columns=normalized_columns,
                references_only_builtin_tables=references_only_builtin_tables,
            )

        errors: list[SqlPreflightError] = []
        for scope in scope_root.traverse():
            physical_sources: dict[str, str] = {}
            referenced_scopes: list[str] = []
            referenced_scope_names: list[str] = []
            referenced_scope_columns: dict[str, list[str]] = {}
            derived_aliases = {
                cls._normalize_identifier(alias)
                for alias, (_node, source) in scope.selected_sources.items()
                if isinstance(alias, str) and alias.strip() and not isinstance(source, exp.Table)
            }
            for alias, (_node, source) in scope.selected_sources.items():
                if not isinstance(source, exp.Table):
                    continue
                identifier = cls._table_identifier(source)
                if cls._is_builtin_table(identifier):
                    continue
                resolved_table = cls._resolve_table_fqn(
                    identifier,
                    normalized_known_tables,
                )
                if resolved_table is None:
                    continue
                table_key = cls._normalize_identifier(resolved_table)
                referenced_scope_columns.setdefault(
                    table_key,
                    cls._column_scope_candidates(
                        table=resolved_table,
                        normalized_columns=normalized_columns,
                    )
                    or [table_key],
                )
                if table_key in referenced_scopes:
                    continue
                referenced_scopes.append(table_key)
                referenced_scope_names.append(resolved_table)
                for candidate in cls._table_alias_candidates(alias=alias, table=source):
                    physical_sources[candidate] = resolved_table

            if not referenced_scopes:
                continue

            select_aliases = {
                cls._normalize_identifier(alias.alias)
                for alias in scope.expression.find_all(exp.Alias)
                if isinstance(alias.alias, str) and alias.alias.strip()
            }
            for column in scope.columns:
                column_name = column.name
                if not column_name:
                    continue
                normalized_column = cls._normalize_identifier(column_name)
                table_name = cls._normalize_identifier(column.table or "")
                if not table_name and normalized_column in select_aliases:
                    continue
                if table_name in derived_aliases:
                    continue
                if table_name:
                    if cls._is_builtin_table_name(table_name):
                        continue
                    resolved = physical_sources.get(table_name) or cls._resolve_table_fqn(
                        table_name,
                        normalized_known_tables,
                    )
                    if resolved is None:
                        continue
                    table_key = cls._normalize_identifier(resolved)
                    available_columns = cls._available_columns_for_scopes(
                        referenced_scope_columns.get(table_key, [table_key]),
                        normalized_columns,
                    )
                    if normalized_column not in available_columns:
                        suggestions = cls._suggest_columns(
                            normalized_column=normalized_column,
                            available_columns=available_columns,
                        )
                        errors.append(
                            SqlPreflightError(
                                code="missing_column",
                                message=(
                                    f"Column '{column_name}' is missing in table '{resolved}'."
                                )
                                + cls._format_suggestion_suffix(suggestions),
                                identifier=column_name,
                                table=resolved,
                                suggestions=suggestions,
                            )
                        )
                    continue

                if references_only_builtin_tables:
                    continue
                candidates = [
                    table_key
                    for table_key in referenced_scopes
                    if normalized_column
                    in cls._available_columns_for_scopes(
                        referenced_scope_columns.get(table_key, [table_key]),
                        normalized_columns,
                    )
                ]
                if len(candidates) == 0:
                    available_columns = cls._available_columns_for_scopes(
                        [
                            scope_key
                            for table_key in referenced_scopes
                            for scope_key in referenced_scope_columns.get(
                                table_key,
                                [table_key],
                            )
                        ],
                        normalized_columns,
                    )
                    suggestions = cls._suggest_columns(
                        normalized_column=normalized_column,
                        available_columns=available_columns,
                    )
                    table_hint = None
                    if len(referenced_scope_names) == 1:
                        table_hint = referenced_scope_names[0]
                    errors.append(
                        SqlPreflightError(
                            code="missing_column",
                            message=(
                                f"Column '{column_name}' was not found in referenced tables."
                            )
                            + cls._format_suggestion_suffix(suggestions),
                            identifier=column_name,
                            table=table_hint,
                            suggestions=suggestions,
                        )
                    )
                elif len(candidates) > 1:
                    errors.append(
                        SqlPreflightError(
                            code="ambiguous_column",
                            message=(
                                f"Column '{column_name}' is ambiguous across tables: "
                                + ", ".join(candidates)
                            ),
                            identifier=column_name,
                        )
                    )
        return errors

    @classmethod
    def _validate_columns_without_scope(
        cls,
        *,
        parsed: exp.Expression,
        normalized_known_tables: dict[str, str],
        normalized_columns: dict[str, set[str]],
        references_only_builtin_tables: bool,
    ) -> list[SqlPreflightError]:
        errors: list[SqlPreflightError] = []
        referenced_scopes = []
        referenced_scope_names = []
        referenced_scope_columns: dict[str, list[str]] = {}
        select_aliases = {
            cls._normalize_identifier(alias.alias)
            for alias in parsed.find_all(exp.Alias)
            if isinstance(alias.alias, str) and alias.alias.strip()
        }
        for reference in cls._extract_table_references_without_scope(parsed):
            resolved_table = cls._resolve_table_fqn(
                reference.identifier,
                normalized_known_tables,
            )
            if resolved_table is None:
                continue
            table_key = cls._normalize_identifier(resolved_table)
            referenced_scopes.append(table_key)
            referenced_scope_names.append(resolved_table)
            referenced_scope_columns.setdefault(
                table_key,
                cls._column_scope_candidates(
                    table=resolved_table,
                    normalized_columns=normalized_columns,
                )
                or [table_key],
            )
        for column in parsed.find_all(exp.Column):
            column_name = column.name
            if not column_name:
                continue
            normalized_column = cls._normalize_identifier(column_name)
            table_name = cls._normalize_identifier(column.table or "")
            if not table_name and normalized_column in select_aliases:
                continue
            if table_name:
                if cls._is_builtin_table_name(table_name):
                    continue
                resolved = cls._resolve_table_fqn(table_name, normalized_known_tables)
                if resolved is None:
                    continue
                table_key = cls._normalize_identifier(resolved)
                available_columns = cls._available_columns_for_scopes(
                    referenced_scope_columns.get(table_key, [table_key]),
                    normalized_columns,
                )
                if normalized_column not in available_columns:
                    suggestions = cls._suggest_columns(
                        normalized_column=normalized_column,
                        available_columns=available_columns,
                    )
                    errors.append(
                        SqlPreflightError(
                            code="missing_column",
                            message=(
                                f"Column '{column_name}' is missing in table '{resolved}'."
                            )
                            + cls._format_suggestion_suffix(suggestions),
                            identifier=column_name,
                            table=resolved,
                            suggestions=suggestions,
                        )
                    )
                continue

            if references_only_builtin_tables:
                continue
            candidates = [
                table_key
                for table_key in referenced_scopes
                if normalized_column
                in cls._available_columns_for_scopes(
                    referenced_scope_columns.get(table_key, [table_key]),
                    normalized_columns,
                )
            ]
            if len(candidates) == 0:
                available_columns = cls._available_columns_for_scopes(
                    [
                        scope_key
                        for table_key in referenced_scopes
                        for scope_key in referenced_scope_columns.get(
                            table_key,
                            [table_key],
                        )
                    ],
                    normalized_columns,
                )
                suggestions = cls._suggest_columns(
                    normalized_column=normalized_column,
                    available_columns=available_columns,
                )
                table_hint = None
                if len(referenced_scope_names) == 1:
                    table_hint = referenced_scope_names[0]
                errors.append(
                    SqlPreflightError(
                        code="missing_column",
                        message=(
                            f"Column '{column_name}' was not found in referenced tables."
                        )
                        + cls._format_suggestion_suffix(suggestions),
                        identifier=column_name,
                        table=table_hint,
                        suggestions=suggestions,
                    )
                )
            elif len(candidates) > 1:
                errors.append(
                    SqlPreflightError(
                        code="ambiguous_column",
                        message=(
                            f"Column '{column_name}' is ambiguous across tables: "
                            + ", ".join(candidates)
                        ),
                        identifier=column_name,
                    )
                )
        return errors

    @classmethod
    def _cross_database_reference_error(
        cls,
        *,
        resolved_tables: list[str] | tuple[str, ...] | set[str],
    ) -> SqlPreflightError | None:
        database_scopes: dict[str, list[str]] = {}
        for resolved_table in resolved_tables:
            mapping = cls._table_mapping_from_fqn(resolved_table)
            if mapping is None:
                continue
            key = f"{mapping['service'].lower()}.{mapping['database'].lower()}"
            database_scopes.setdefault(key, []).append(resolved_table)
        if len(database_scopes) <= 1:
            return None

        scope_list = sorted(
            {
                f"{mapping['service']}.{mapping['database']}"
                for mapping in (
                    cls._table_mapping_from_fqn(table)
                    for tables in database_scopes.values()
                    for table in tables
                )
                if mapping is not None
            }
        )
        example_tables = sorted(
            {
                table
                for tables in database_scopes.values()
                for table in tables
            }
        )[:4]
        return SqlPreflightError(
            code="cross_database_reference",
            message=(
                "Cross-database SQL is not allowed in one query. "
                f"Resolved databases: {', '.join(scope_list)}. "
                f"Referenced tables: {', '.join(example_tables)}."
            ),
        )

    @classmethod
    def _table_alias_candidates(
        cls,
        *,
        alias: str | None,
        table: exp.Table,
    ) -> set[str]:
        candidates = {
            cls._normalize_identifier(value)
            for value in [
                alias,
                table.name,
                cls._table_identifier(table),
                cls._join_identifier_parts([table.db, table.name]),
                cls._join_identifier_parts([table.catalog, table.db, table.name]),
            ]
            if isinstance(value, str) and value.strip()
        }
        return {candidate for candidate in candidates if candidate}

    @classmethod
    def _table_identifier(cls, table: exp.Table) -> str:
        return cls._join_identifier_parts([table.catalog, table.db, table.name])

    @staticmethod
    def _join_identifier_parts(parts: list[str | None]) -> str:
        return ".".join(part.strip() for part in parts if isinstance(part, str) and part.strip())

    @staticmethod
    def _optional_identifier(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _table_mapping_from_fqn(table: str) -> dict[str, str] | None:
        parts = [part.strip() for part in table.split(".") if part.strip()]
        if len(parts) < 4:
            return None
        return {
            "service": parts[0],
            "database": parts[1],
            "schema": parts[2],
            "table": parts[-1],
        }

    @classmethod
    def _is_builtin_table(cls, table: str) -> bool:
        return cls._is_builtin_table_name(cls._normalize_identifier(table))

    @staticmethod
    def _is_builtin_table_name(table: str) -> bool:
        normalized = table.strip().lower()
        return normalized.startswith((
            "system.",
            "information_schema.",
            "pg_catalog.",
        ))

    @staticmethod
    def _is_introspection_sql(sql: str) -> bool:
        normalized = sql.lstrip().lower()
        return normalized.startswith((
            "show ",
            "show\n",
            "describe ",
            "describe\n",
            "desc ",
            "desc\n",
            "exists ",
            "exists\n",
            "explain ",
            "explain\n",
        ))

    def check_metric_consistency(
        self,
        *,
        sql: str,
        metric_id: str | None,
        metric_name: str | None,
        metric_allowed_dimensions: list[str],
        metric_source_tables: list[str],
        context_tables: list[str],
        metadata_table_columns: dict[str, set[str]],
    ) -> SqlPreflightResult:
        """Check SQL query consistency against a metric's governance contract.

        Validates that:
        - All column references are in allowed_dimensions
        - No query references tables outside source_tables

        This method enables governed metric validation during SQL planning.

        Args:
            sql: The SQL query to validate.
            metric_id: The metric identifier (for error messages).
            metric_name: The metric name (for error messages).
            metric_allowed_dimensions: Allowed dimension columns for the metric.
            metric_source_tables: Allowed source tables for the metric.
            context_tables: Available tables in the metadata context.
            metadata_table_columns: Map of table FQNs to their column sets.

        Returns:
            SqlPreflightResult with pass/fail status and any metric violations.
        """
        errors: list[SqlPreflightError] = []

        # Check for unauthorized table references
        table_pattern = re.compile(
            r"\b(?:FROM|JOIN)\s+(?:[\w.]+\s+AS\s+)?([\w.]+)",
            re.IGNORECASE,
        )
        for match in table_pattern.finditer(sql):
            table_ref = match.group(1).lower().strip()
            # Use word-boundary regex to prevent substring matches
            # e.g., "orders" should NOT match "fraud_orders"
            is_authorized = any(
                re.search(rf"\b{re.escape(table_ref)}\b", source.lower())
                is not None
                for source in metric_source_tables
            )
            if not is_authorized:
                errors.append(
                    SqlPreflightError(
                        code="metric_source_table_violation",
                        message=(
                            f"Metric '{metric_name or metric_id}' only allows source tables: "
                            f"{', '.join(metric_source_tables)}. "
                            f"Query references unauthorized table: '{table_ref}'."
                        ),
                        table=table_ref,
                        suggestions=list(metric_source_tables),
                    )
                )

        # Check for disallowed dimension columns
        normalized_allowed = {col.lower().strip() for col in metric_allowed_dimensions}

        # Extract table-qualified column references
        column_pattern = re.compile(r"\b([\w]+)\s*\.\s*([\w]+)", re.IGNORECASE)
        for match in column_pattern.finditer(sql):
            table_ref = match.group(1).lower().strip()
            col_name = match.group(2).lower().strip()

            # Find the resolved table from metadata
            resolved_table = self._resolve_table_fqn(
                table_ref,
                {
                    self._normalize_identifier(t): t
                    for t in context_tables
                    if t and t.strip()
                },
            )
            if resolved_table is None:
                resolved_table = table_ref

            # Check if this column is allowed for the metric
            if (
                col_name not in normalized_allowed
                and col_name not in {
                    "as", "distinct", "count", "sum", "avg", "min", "max",
                    "and", "or", "not", "in", "between", "like", "is", "null",
                }
            ):
                errors.append(
                    SqlPreflightError(
                        code="metric_dimension_violation",
                        message=(
                            f"Metric '{metric_name or metric_id}' only allows dimensions: "
                            f"{', '.join(metric_allowed_dimensions)}. "
                            f"Query references disallowed column: '{col_name}'."
                        ),
                        identifier=col_name,
                        table=resolved_table,
                        suggestions=list(metric_allowed_dimensions),
                    )
                )

        return SqlPreflightResult(
            status="fail" if errors else "pass",
            errors=errors,
            referenced_tables=[],
            referenced_columns=[],
        )
