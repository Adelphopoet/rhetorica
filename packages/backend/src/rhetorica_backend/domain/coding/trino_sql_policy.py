"""Fail-closed SQL policy for coding traffic sent to Trino."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rhetorica_backend.sql_guard.service import SqlParserPrimitives
from sqlglot import exp
from sqlglot.errors import ParseError


@dataclass(frozen=True)
class StrictSqlDecision:
    allowed: bool
    sql: str | None
    reason: str | None = None


_DISALLOWED_NODE_KEYS = frozenset({
    "alter",
    "analyze",
    "attach",
    "cache",
    "call",
    "command",
    "copy",
    "create",
    "delete",
    "detach",
    "drop",
    "execute",
    "grant",
    "insert",
    "into",
    "kill",
    "load_data",
    "lock",
    "merge",
    "optimize",
    "prepare",
    "revoke",
    "set",
    "transaction",
    "truncate",
    "uncache",
    "update",
    "use",
})

# Every callable expression must be explicitly present here. sqlglot maps many
# built-ins to typed nodes; Anonymous covers the remaining Trino names.
_ALLOWED_BUILTIN_FUNCTIONS = frozenset({
    "ABS",
    "ANY_VALUE",
    "AND",
    "APPROX_DISTINCT",
    "APPROX_PERCENTILE",
    "ARRAY",
    "ARRAY_AGG",
    "ARRAY_DISTINCT",
    "ARRAY_JOIN",
    "ARRAY_LENGTH",
    "ARRAY_MAX",
    "ARRAY_MIN",
    "AT_TIMEZONE",
    "AVG",
    "BOOL_AND",
    "BOOL_OR",
    "CARDINALITY",
    "CASE",
    "CAST",
    "CEIL",
    "CEILING",
    "CHECKSUM",
    "COALESCE",
    "CONCAT",
    "CONCAT_WS",
    "COUNT",
    "COUNT_IF",
    "CUME_DIST",
    "CURRENT_DATE",
    "CURRENT_TIMESTAMP",
    "DATE",
    "DATE_ADD",
    "DATE_DIFF",
    "DATE_FORMAT",
    "DATE_PARSE",
    "DATE_TRUNC",
    "DAY",
    "DAY_OF_MONTH",
    "DAY_OF_WEEK",
    "DAY_OF_YEAR",
    "DECODE",
    "DENSE_RANK",
    "ELEMENT_AT",
    "ENCODE",
    "EVERY",
    "FIRST_VALUE",
    "FLOOR",
    "FORMAT",
    "FROM_ISO8601_DATE",
    "FROM_ISO8601_TIMESTAMP",
    "FROM_HEX",
    "FROM_UTF8",
    "GREATEST",
    "HISTOGRAM",
    "HEX",
    "HOUR",
    "IF",
    "LAG",
    "LAST_VALUE",
    "LEAD",
    "LEAST",
    "LENGTH",
    "LOWER",
    "LPAD",
    "LTRIM",
    "MAP",
    "MAP_AGG",
    "MAP_KEYS",
    "MAP_VALUES",
    "MAX",
    "MAX_BY",
    "MD5",
    "MD5_DIGEST",
    "MIN",
    "MINUTE",
    "MIN_BY",
    "MONTH",
    "MULTIMAP_AGG",
    "NTH_VALUE",
    "NTILE",
    "NULLIF",
    "OR",
    "PERCENT_RANK",
    "POSITION",
    "QUARTER",
    "RANK",
    "REGEXP_EXTRACT",
    "REGEXP_LIKE",
    "REGEXP_REPLACE",
    "REPLACE",
    "ROUND",
    "ROW_NUMBER",
    "RPAD",
    "RTRIM",
    "SECOND",
    "SHA",
    "SHA1",
    "SHA2",
    "SHA256",
    "SHA512",
    "SPLIT",
    "SPLIT_PART",
    "STRPOS",
    "SUBSTR",
    "SUBSTRING",
    "SUM",
    "TO_CHAR",
    "TO_HEX",
    "TO_ISO8601",
    "TO_UTF8",
    "TRIM",
    "TRY",
    "TRY_CAST",
    "TYPEOF",
    "UPPER",
    "UNHEX",
    # Read-only array expansion used by existing dbt models.
    "UNNEST",
    "WEEK",
    "WITH_TIMEZONE",
    "YEAR",
    "XXHASH64",
})

_IDENTIFIER = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'
_QUALIFIED_IDENTIFIER = rf"{_IDENTIFIER}(?:\s*\.\s*{_IDENTIFIER}){{0,2}}"
_LIKE = r"(?:\s+LIKE\s+(?:'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"))?"
_METADATA_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        rf"SHOW\s+CATALOGS{_LIKE}",
        rf"SHOW\s+SCHEMAS(?:\s+(?:FROM|IN)\s+{_QUALIFIED_IDENTIFIER})?{_LIKE}",
        rf"SHOW\s+TABLES(?:\s+(?:FROM|IN)\s+{_QUALIFIED_IDENTIFIER})?{_LIKE}",
        rf"SHOW\s+COLUMNS\s+(?:FROM|IN)\s+{_QUALIFIED_IDENTIFIER}{_LIKE}",
        rf"SHOW\s+FUNCTIONS{_LIKE}",
        rf"SHOW\s+CREATE\s+(?:TABLE|VIEW|MATERIALIZED\s+VIEW)\s+{_QUALIFIED_IDENTIFIER}",
    )
)
_EXPLAIN = re.compile(
    r"EXPLAIN\s*"
    r"(?:\(\s*(?:TYPE\s+(?:LOGICAL|DISTRIBUTED|VALIDATE|IO))"
    r"(?:\s*,\s*FORMAT\s+(?:TEXT|GRAPHVIZ|JSON))?\s*\)\s*)?"
    r"(?P<query>.+)",
    re.IGNORECASE | re.DOTALL,
)


class StrictTrinoReadOnlyPolicy:
    """Allow only one fully parsed read-only statement without rewriting it."""

    def __init__(self, parser: SqlParserPrimitives | None = None) -> None:
        self._parser = parser or SqlParserPrimitives()

    def evaluate(self, sql: str) -> StrictSqlDecision:
        raw = sql.strip()
        if not raw:
            return self._deny("empty_sql")
        if self._metadata_statement(raw):
            return StrictSqlDecision(allowed=True, sql=raw)
        explain = _EXPLAIN.fullmatch(raw)
        if explain is not None:
            nested = self._evaluate_query(explain.group("query").strip())
            if not nested.allowed:
                return self._deny(f"explain_{nested.reason}")
            return StrictSqlDecision(allowed=True, sql=raw)
        if raw[:7].upper() == "EXPLAIN":
            return self._deny("explain_not_allowed")
        return self._evaluate_query(raw)

    def _evaluate_query(self, raw: str) -> StrictSqlDecision:
        try:
            statement = self._parser.parse_single(raw, dialect="trino")
        except ParseError:
            return self._deny("parse_error")
        except ValueError as exc:
            return self._deny(str(exc))
        if isinstance(statement, exp.Describe):
            if isinstance(statement.this, exp.Table):
                return StrictSqlDecision(allowed=True, sql=raw)
            return self._deny("describe_target_not_allowed")
        if not isinstance(statement, exp.Query):
            return self._deny("statement_not_read_only")
        for node in statement.walk():
            if node.key.lower() in _DISALLOWED_NODE_KEYS:
                return self._deny(f"node_not_allowed:{node.key.lower()}")
            if isinstance(node, exp.Table) and isinstance(node.this, exp.Func):
                return self._deny("table_function_not_allowed")
            if isinstance(node, exp.Dot) and any(
                isinstance(child, exp.Func) for child in node.walk()
            ):
                return self._deny("qualified_function_not_allowed")
            if isinstance(node, exp.Func):
                name = self._function_name(node)
                if name not in _ALLOWED_BUILTIN_FUNCTIONS:
                    return self._deny(f"function_not_allowed:{name.lower()}")
        return StrictSqlDecision(allowed=True, sql=raw)

    @staticmethod
    def _function_name(node: exp.Func) -> str:
        if isinstance(node, exp.Anonymous):
            return str(node.this).strip().upper()
        return str(node.sql_name()).strip().upper()

    @staticmethod
    def _metadata_statement(raw: str) -> bool:
        return any(pattern.fullmatch(raw) is not None for pattern in _METADATA_PATTERNS)

    @staticmethod
    def _deny(reason: str) -> StrictSqlDecision:
        return StrictSqlDecision(allowed=False, sql=None, reason=reason)
