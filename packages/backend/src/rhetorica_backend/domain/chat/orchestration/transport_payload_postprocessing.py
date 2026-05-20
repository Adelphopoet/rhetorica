"""Focused transport payload postprocessing services."""

from __future__ import annotations

import re
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import sql_dialect_for_engine
from rhetorica_backend.contracts.sql_guard import SqlGuardRequest
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.sql_guard.service import SqlGuardService

SQL_CODE_BLOCK_PATTERN = re.compile(
    r"```(?:sql)?\s*(.*?)```",
    flags=re.IGNORECASE | re.DOTALL,
)
ANSWER_REFERENCE_PATTERN = re.compile(
    r"\b(?:мой ответ выше|my answer above|as mentioned above)\b",
    flags=re.IGNORECASE,
)


class SqlAnswerPostprocessingService:
    """Applies SQL safety and answer cleanup after shared turn assembly."""

    def __init__(self, *, sql_router: SqlRouter) -> None:
        self._sql_router = sql_router

    def postprocess(
        self,
        *,
        request: ChatAskRequest,
        llm_payload: dict[str, Any],
        db_connectors: list[ConnectorItem],
        warnings: list[str],
    ) -> dict[str, Any]:
        payload = dict(llm_payload)
        sql_candidates = self.sql_candidates_from_payload(payload)

        if not sql_candidates:
            return payload

        guard = SqlGuardService()
        guard_dialect = self.resolve_guard_dialect(
            request=request,
            llm_payload=payload,
            db_connectors=db_connectors,
        )
        first_safe_sql: str | None = None
        for sql_candidate in sql_candidates:
            decision = guard.guard(SqlGuardRequest(sql=sql_candidate, dialect=guard_dialect))
            if decision.allowed:
                if first_safe_sql is None:
                    first_safe_sql = sql_candidate.strip()
                continue

            violation_codes = {violation.code for violation in decision.violations}
            if violation_codes & {"NON_READ_ONLY", "MULTI_STATEMENT"}:
                if "unsafe_sql_generation_blocked" not in warnings:
                    warnings.append("unsafe_sql_generation_blocked")
                payload["answer"] = (
                    "Я не помогу с destructive или mutating SQL. "
                    "Сформулируй read-only запрос, и я соберу безопасный SELECT."
                )
                payload["sql"] = None
                payload["generated_sql"] = None
                payload["execution"] = None
                payload["claims"] = []
                payload["evidence"] = []
                payload["claim_evidence_links"] = []
                payload["uncertainty_flags"] = ["unsafe_sql_request_blocked"]
                return payload

        if first_safe_sql is not None and self.extract_sql(payload) is None:
            payload["sql"] = first_safe_sql
        return payload

    def resolve_guard_dialect(
        self,
        *,
        request: ChatAskRequest,
        llm_payload: dict[str, Any],
        db_connectors: list[ConnectorItem],
    ) -> str:
        selected_connector = self._sql_router.select_connector(
            request=request,
            llm_payload=llm_payload,
            db_connectors=db_connectors,
        )
        if selected_connector is not None:
            return sql_dialect_for_engine(selected_connector.connection.engine)
        if request.database_connector_name:
            for connector in db_connectors:
                if connector.name == request.database_connector_name:
                    return sql_dialect_for_engine(connector.connection.engine)
        return sql_dialect_for_engine(db_connectors[0].connection.engine) if db_connectors else "clickhouse"

    @staticmethod
    def extract_sql(llm_payload: dict[str, Any]) -> str | None:
        sql = llm_payload.get("sql") or llm_payload.get("generated_sql")
        return sql if isinstance(sql, str) else None

    @staticmethod
    def sql_candidates_from_payload(llm_payload: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        for key in ("sql", "generated_sql"):
            value = llm_payload.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
        return candidates

    @classmethod
    def sql_candidates_from_answer(cls, answer: str) -> list[str]:
        candidates: list[str] = []
        for match in SQL_CODE_BLOCK_PATTERN.findall(answer):
            candidate = match.strip()
            if cls.looks_like_sql(candidate):
                candidates.append(candidate)
        return candidates

    @staticmethod
    def looks_like_sql(value: str) -> bool:
        normalized = value.lstrip().lower()
        return normalized.startswith((
            "select ",
            "with ",
            "show ",
            "describe ",
            "desc ",
            "explain ",
            "exists ",
            "insert ",
            "update ",
            "delete ",
            "drop ",
            "alter ",
            "truncate ",
            "create ",
            "grant ",
            "revoke ",
        ))

    @staticmethod
    def _to_optional_str(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None
