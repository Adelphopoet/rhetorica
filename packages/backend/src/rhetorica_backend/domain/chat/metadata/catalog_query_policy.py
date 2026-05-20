"""Query-shaping policy for OpenMetadata catalog search."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class MetadataCatalogSearchRequest:
    """Prepared OpenMetadata search request."""

    query_text: str
    query_filter: str
    entity_type: str | None


class MetadataCatalogQueryPolicy:
    """Own keyword shaping and filter generation for metadata search."""

    _ALLOWED_ENTITY_TYPES: ClassVar[set[str]] = {
        "table",
        "dashboard",
        "pipeline",
        "chart",
        "topic",
        "database",
        "databaseSchema",
        "glossary",
        "glossaryTerm",
        "metric",
        "container",
        "searchIndex",
        "mlmodel",
        "dataProduct",
        "domain",
        "user",
        "team",
    }
    _DEFAULT_DISCOVERY_ENTITY_TYPES: ClassVar[tuple[str, ...]] = (
        "table",
        "glossaryTerm",
        "metric",
        "dashboard",
        "pipeline",
        "chart",
        "topic",
        "database",
        "databaseSchema",
        "searchIndex",
        "mlmodel",
        "dataProduct",
        "domain",
    )
    _SEARCH_SYNTAX_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\w+\s*:\s*\S+|"
        r"\b(?:AND|OR|NOT)\b|"
        r"[*?]|"
        r"[()]|"
        r"\"|"
        r"\[.+\s+TO\s+.+\]|"
        r"[+\-~^]"
    )
    _IDENTIFIER_QUERY_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[a-z0-9_.]+")
    _TOKEN_SPLIT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[^\w]+", re.UNICODE)

    @classmethod
    def normalize_entity_type(cls, value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip()
        canonical = normalized[0].lower() + normalized[1:] if normalized else normalized
        lowered = normalized.lower()
        if lowered in cls._ALLOWED_ENTITY_TYPES:
            return lowered
        return canonical if canonical in cls._ALLOWED_ENTITY_TYPES else None

    @classmethod
    def build_search_request(
        cls,
        *,
        entity_type: str | None,
        query: str | None,
        service: str | None = None,
        database: str | None = None,
        schema: str | None = None,
    ) -> MetadataCatalogSearchRequest:
        normalized_entity_type = cls.normalize_entity_type(entity_type)
        effective_entity_type = cls._effective_entity_type(
            entity_type=normalized_entity_type,
            query=query,
        )
        return MetadataCatalogSearchRequest(
            query_text=cls.build_mcp_search_query(
                entity_type=effective_entity_type,
                query=query,
            ),
            query_filter=cls.build_mcp_query_filter(
                entity_type=effective_entity_type,
                query=query,
                service=service,
                database=database,
                schema=schema,
            ),
            entity_type=effective_entity_type,
        )

    @classmethod
    def build_mcp_search_query(
        cls,
        *,
        entity_type: str | None,
        query: str | None,
    ) -> str:
        _ = entity_type
        if not isinstance(query, str) or not query.strip():
            return "*"
        stripped = query.strip()
        if cls.has_explicit_search_syntax(stripped):
            return stripped
        if cls.is_identifier_query(stripped):
            return f"*{stripped.lower()}*"
        tokens = cls.extract_query_tokens(stripped)
        if len(tokens) == 1:
            return f"*{tokens[0]}*"
        return "*"

    @classmethod
    def build_mcp_query_filter(
        cls,
        *,
        entity_type: str | None,
        query: str | None = None,
        service: str | None = None,
        database: str | None = None,
        schema: str | None = None,
    ) -> str:
        must: list[dict[str, Any]] = []
        if entity_type is not None:
            must.append({"term": {"entityType": entity_type}})
        else:
            must.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"entityType": candidate}}
                            for candidate in cls._DEFAULT_DISCOVERY_ENTITY_TYPES
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

        must.extend(
            cls._scope_filter_clauses(
                service=service,
                database=database,
                schema=schema,
            )
        )

        if isinstance(query, str) and query.strip() and not cls.has_explicit_search_syntax(query):
            normalized_query = query.strip().lower()
            if cls.is_identifier_query(normalized_query):
                must.append(
                    {
                        "bool": {
                            "should": cls._identifier_query_clauses(normalized_query),
                            "minimum_should_match": 1,
                        }
                    }
                )
            else:
                tokens = cls.extract_query_tokens(normalized_query)
                if tokens:
                    must.append(
                        {
                            "bool": {
                                "should": cls._token_query_clauses(
                                    tokens=tokens,
                                    entity_type=entity_type,
                                ),
                                "minimum_should_match": 1,
                            }
                        }
                    )

        if not must:
            return json.dumps({"match_all": {}}, ensure_ascii=False)
        return json.dumps({"bool": {"must": must}}, ensure_ascii=False)

    @classmethod
    def is_identifier_query(cls, query: str | None) -> bool:
        if not isinstance(query, str) or not query.strip():
            return False
        return bool(cls._IDENTIFIER_QUERY_PATTERN.fullmatch(query.strip().lower()))

    @classmethod
    def has_explicit_search_syntax(cls, query: str | None) -> bool:
        if not isinstance(query, str) or not query.strip():
            return False
        return bool(cls._SEARCH_SYNTAX_PATTERN.search(query.replace("%20", " ").strip()))

    @classmethod
    def extract_query_tokens(cls, query: str | None) -> list[str]:
        if not isinstance(query, str) or not query.strip():
            return []
        deduped: list[str] = []
        seen: set[str] = set()
        for raw_token in cls._TOKEN_SPLIT_PATTERN.split(query.strip().lower()):
            token = raw_token.strip("._")
            if len(token) < 2:
                continue
            if token in seen:
                continue
            seen.add(token)
            deduped.append(token)
        return deduped[:6]

    @classmethod
    def _effective_entity_type(
        cls,
        *,
        entity_type: str | None,
        query: str | None,
    ) -> str | None:
        if entity_type is not None:
            return entity_type
        if not isinstance(query, str) or not query.strip():
            return None
        if cls.has_explicit_search_syntax(query):
            return None
        tokens = cls.extract_query_tokens(query)
        if len(tokens) == 1:
            return "table"
        return None

    @classmethod
    def _normalize_scope_value(cls, value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        candidate = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]+", candidate):
            return None
        return candidate

    @classmethod
    def _scope_filter_clauses(
        cls,
        *,
        service: str | None,
        database: str | None,
        schema: str | None,
    ) -> list[dict[str, Any]]:
        clauses: list[dict[str, Any]] = []
        normalized_service = cls._normalize_scope_value(service)
        normalized_database = cls._normalize_scope_value(database)
        normalized_schema = cls._normalize_scope_value(schema)

        if normalized_service:
            clauses.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"service.name": normalized_service}},
                            {"term": {"service.displayName.keyword": normalized_service}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        if normalized_database:
            clauses.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"database.name.keyword": normalized_database}},
                            {"term": {"database.displayName.keyword": normalized_database}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        if normalized_schema:
            clauses.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"databaseSchema.name.keyword": normalized_schema}},
                            {"term": {"databaseSchema.displayName.keyword": normalized_schema}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        return clauses

    @staticmethod
    def _identifier_query_clauses(query: str) -> list[dict[str, Any]]:
        return [
            {"term": {"name.keyword": query}},
            {"term": {"displayName.keyword": query}},
            {"term": {"fullyQualifiedName": query}},
            {"match": {"displayName": query}},
            {"match": {"fullyQualifiedName": query}},
            {"match": {"name.ngram": query}},
        ]

    @classmethod
    def _token_query_clauses(
        cls,
        *,
        tokens: list[str],
        entity_type: str | None,
    ) -> list[dict[str, Any]]:
        fields = [
            "name",
            "displayName",
            "fullyQualifiedName",
            "description",
        ]
        if entity_type in (None, "table"):
            fields.extend(["name.ngram", "columns.name", "columns.description"])
        return [{"match": {field_name: token}} for token in tokens for field_name in fields]
