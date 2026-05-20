from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from rhetorica_backend.contracts.sql_guard import SqlGuardRequest, SqlGuardResponse
from rhetorica_backend.sql_guard.models import SqlGuardViolation

_DISALLOWED_NODE_KEYS = {
    "alter",
    "analyze",
    "attach",
    "cache",
    "command",
    "copy",
    "create",
    "delete",
    "detach",
    "drop",
    "execute",
    "grant",
    "insert",
    "kill",
    "load_data",
    "merge",
    "optimize",
    "revoke",
    "truncate",
    "uncache",
    "update",
    "use",
}

_ALLOWED_ROOT_KEYS = {
    "describe",
    "except",
    "intersect",
    "select",
    "subquery",
    "union",
    "values",
}

_ALLOWED_INTROSPECTION_PREFIXES = (
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
)


class SqlRowLimitNormalizer:
    """Applies bounded row-limit policy without producing engine-invalid SQL."""

    def normalize(
        self,
        *,
        statement: exp.Query,
        row_limit: int,
        engine: str | None,
    ) -> tuple[exp.Query, list[str], int | None]:
        warnings: list[str] = []
        normalized_stmt = cast(exp.Query, statement.copy())
        limit_node = normalized_stmt.args.get("limit")

        if self._should_preserve_unlimited(statement=normalized_stmt, engine=engine):
            return normalized_stmt, warnings, None

        if limit_node is None:
            normalized_stmt = self._with_limit(normalized_stmt, row_limit)
            warnings.append(f"Applied default LIMIT {row_limit}.")
            return normalized_stmt, warnings, row_limit

        parsed_limit = self._parse_limit_value(limit_node)
        if parsed_limit is None:
            normalized_stmt = self._with_limit(normalized_stmt, row_limit)
            warnings.append(
                f"Could not statically evaluate LIMIT value. Replaced with policy LIMIT {row_limit}."
            )
            return normalized_stmt, warnings, row_limit

        if parsed_limit > row_limit:
            normalized_stmt = self._with_limit(normalized_stmt, row_limit)
            warnings.append(f"Clamped LIMIT from {parsed_limit} to policy LIMIT {row_limit}.")
            return normalized_stmt, warnings, row_limit

        return normalized_stmt, warnings, parsed_limit

    @classmethod
    def _should_preserve_unlimited(cls, *, statement: exp.Query, engine: str | None) -> bool:
        if str(engine or "").strip().lower() != "impala":
            return False
        return cls._is_relationless_select(statement)

    @staticmethod
    def _is_relationless_select(statement: exp.Query) -> bool:
        if not isinstance(statement, exp.Select):
            return False
        return statement.args.get("from") is None

    @staticmethod
    def _with_limit(statement: exp.Query, value: int) -> exp.Query:
        statement.set("limit", exp.Limit(expression=exp.Literal.number(value)))
        return statement

    @staticmethod
    def _parse_limit_value(limit_node: exp.Expression) -> int | None:
        expression = limit_node.args.get("expression")
        if expression is None or not isinstance(expression, exp.Literal):
            return None
        if not expression.is_int:
            return None
        return int(expression.this)


class SqlGuardService:
    """Guards SQL statements before execution against read-only policy."""

    def __init__(self, row_limit_normalizer: SqlRowLimitNormalizer | None = None) -> None:
        self._row_limit_normalizer = row_limit_normalizer or SqlRowLimitNormalizer()

    def guard(self, request: SqlGuardRequest) -> SqlGuardResponse:
        raw_sql = request.sql.strip()
        if not raw_sql:
            return self._blocked(
                request=request,
                violations=[
                    SqlGuardViolation(
                        code="EMPTY_SQL",
                        message="SQL string is empty after trimming whitespace.",
                    )
                ],
            )

        try:
            statements = parse(raw_sql, read=request.dialect)
        except ParseError as exc:
            if self._is_allowed_introspection_sql(raw_sql):
                return self._allow_introspection(request=request, sql=raw_sql)
            return self._blocked(
                request=request,
                violations=[
                    SqlGuardViolation(
                        code="PARSE_ERROR",
                        message=f"Failed to parse SQL for dialect '{request.dialect}': {exc}",
                    )
                ],
            )

        if len(statements) != 1:
            return self._blocked(
                request=request,
                violations=[
                    SqlGuardViolation(
                        code="MULTI_STATEMENT",
                        message="Exactly one SQL statement is allowed.",
                    )
                ],
            )

        statement = statements[0]
        if statement is None:
            return self._blocked(
                request=request,
                violations=[
                    SqlGuardViolation(
                        code="PARSE_ERROR",
                        message="Parser returned an empty statement.",
                    )
                ],
            )

        root_key = statement.key.lower()
        if self._is_allowed_introspection_statement(statement=statement, raw_sql=raw_sql):
            return self._allow_introspection(request=request, sql=raw_sql)

        if root_key not in _ALLOWED_ROOT_KEYS:
            return self._blocked(
                request=request,
                violations=[
                    SqlGuardViolation(
                        code="NON_READ_ONLY",
                        message=f"Only read-only SQL is allowed. Got root statement '{root_key}'.",
                    )
                ],
            )

        if not isinstance(statement, exp.Query):
            return self._blocked(
                request=request,
                violations=[
                    SqlGuardViolation(
                        code="NON_READ_ONLY",
                        message=f"Statement '{root_key}' is not a query expression.",
                    )
                ],
            )

        violations = list(self._detect_disallowed_nodes(statement.walk()))
        if violations:
            return self._blocked(request=request, violations=violations)

        normalized_stmt, warnings, applied_limit = self._row_limit_normalizer.normalize(
            statement=statement,
            row_limit=request.policy.row_limit,
            engine=request.engine,
        )

        return SqlGuardResponse(
            allowed=True,
            dialect=request.dialect,
            normalized_sql=normalized_stmt.sql(dialect=request.dialect, pretty=False),
            applied_row_limit=applied_limit,
            warnings=warnings,
            violations=[],
            policy=request.policy,
        )

    def _detect_disallowed_nodes(
        self, nodes: Iterable[exp.Expression]
    ) -> Iterable[SqlGuardViolation]:
        violations: list[SqlGuardViolation] = []
        for node in nodes:
            node_key = node.key.lower()
            if node_key in _DISALLOWED_NODE_KEYS:
                violations.append(
                    SqlGuardViolation(
                        code="NON_READ_ONLY",
                        message=f"Read-only policy violation: '{node_key}' is not allowed.",
                    )
                )
                break
        return violations

    @classmethod
    def _is_allowed_introspection_statement(
        cls,
        *,
        statement: exp.Expression,
        raw_sql: str,
    ) -> bool:
        root_key = statement.key.lower()
        if root_key == "describe":
            return True
        if root_key != "command":
            return False
        return cls._is_allowed_introspection_sql(raw_sql)

    @staticmethod
    def _is_allowed_introspection_sql(sql: str) -> bool:
        normalized = sql.lstrip().lower()
        return normalized.startswith(_ALLOWED_INTROSPECTION_PREFIXES)

    @staticmethod
    def _allow_introspection(
        *,
        request: SqlGuardRequest,
        sql: str,
    ) -> SqlGuardResponse:
        return SqlGuardResponse(
            allowed=True,
            dialect=request.dialect,
            normalized_sql=sql,
            applied_row_limit=None,
            warnings=[],
            violations=[],
            policy=request.policy,
        )

    @staticmethod
    def _blocked(
        request: SqlGuardRequest,
        violations: list[SqlGuardViolation],
    ) -> SqlGuardResponse:
        return SqlGuardResponse(
            allowed=False,
            dialect=request.dialect,
            normalized_sql=None,
            applied_row_limit=None,
            warnings=[],
            violations=violations,
            policy=request.policy,
        )
