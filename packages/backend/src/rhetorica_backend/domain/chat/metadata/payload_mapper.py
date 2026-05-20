"""Payload normalization for metadata sources."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.domain.chat.metadata.models import (
    DataQualitySummary,
    MetadataEntityReference,
    MetadataMetricReference,
    TableContext,
)


class MetadataPayloadMapper:
    """Map OM and MCP payloads into chat-facing metadata models."""

    @staticmethod
    def to_optional_str(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def extract_nested_name(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        candidate = value.get("displayName") or value.get("name")
        return candidate if isinstance(candidate, str) and candidate.strip() else None

    @classmethod
    def extract_entity_names(cls, value: Any) -> list[str]:
        if isinstance(value, list):
            names: list[str] = []
            for item in value:
                names.extend(cls.extract_entity_names(item))
            return names
        if isinstance(value, dict):
            for key in ("displayName", "name", "fullyQualifiedName"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    clean = candidate.strip()
                    return [clean.split(".")[-1] if key == "fullyQualifiedName" else clean]
        if isinstance(value, str) and value.strip():
            return [value.strip().split(".")[-1]]
        return []

    @classmethod
    def extract_entity_refs(cls, value: Any) -> list[MetadataEntityReference]:
        if isinstance(value, list):
            refs: list[MetadataEntityReference] = []
            seen: set[tuple[str | None, str]] = set()
            for item in value:
                for ref in cls.extract_entity_refs(item):
                    identity = (ref.type, ref.fully_qualified_name or ref.name)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    refs.append(ref)
            return refs
        if isinstance(value, dict):
            name = cls.to_optional_str(value.get("name"))
            fqn = cls.to_optional_str(value.get("fullyQualifiedName"))
            display_name = cls.to_optional_str(value.get("displayName"))
            ref_type = cls.to_optional_str(value.get("type") or value.get("entityType"))
            resolved_name = display_name or name or (fqn.split(".")[-1] if fqn else None)
            if resolved_name:
                return [
                    MetadataEntityReference(
                        name=resolved_name,
                        type=ref_type,
                        display_name=display_name,
                        fully_qualified_name=fqn,
                    )
                ]
        if isinstance(value, str) and value.strip():
            clean = value.strip()
            return [MetadataEntityReference(name=clean.split(".")[-1], fully_qualified_name=clean)]
        return []

    @classmethod
    def extract_owner_teams(cls, value: Any) -> list[str]:
        return [
            ref.name
            for ref in cls.extract_entity_refs(value)
            if (ref.type or "").strip().lower() == "team"
        ]

    @classmethod
    def extract_glossary_terms(cls, item: dict[str, Any]) -> list[MetadataEntityReference]:
        explicit = cls.extract_entity_refs(
            item.get("glossaryTerms")
            or item.get("glossary_terms")
            or item.get("terms")
        )
        tag_terms: list[MetadataEntityReference] = []
        raw_tags = item.get("tags")
        if isinstance(raw_tags, list):
            for tag in raw_tags:
                if not isinstance(tag, dict):
                    continue
                source = cls.to_optional_str(tag.get("source"))
                if source and source.lower() != "glossary":
                    continue
                tag_fqn = cls.to_optional_str(tag.get("tagFQN"))
                if not tag_fqn:
                    continue
                tag_terms.append(
                    MetadataEntityReference(
                        name=tag_fqn.split(".")[-1],
                        type="glossaryTerm",
                        fully_qualified_name=tag_fqn,
                    )
                )
        return cls._unique_entity_refs([*explicit, *tag_terms])

    @classmethod
    def extract_metric_refs(cls, value: Any) -> list[MetadataMetricReference]:
        if isinstance(value, list):
            refs: list[MetadataMetricReference] = []
            seen: set[str] = set()
            for item in value:
                for ref in cls.extract_metric_refs(item):
                    identity = ref.fully_qualified_name or ref.name
                    if identity in seen:
                        continue
                    seen.add(identity)
                    refs.append(ref)
            return refs
        if isinstance(value, dict):
            name = cls.to_optional_str(value.get("name"))
            fqn = cls.to_optional_str(value.get("fullyQualifiedName") or value.get("fqn"))
            display_name = cls.to_optional_str(value.get("displayName"))
            description = cls.to_optional_str(value.get("description"))
            resolved_name = display_name or name or (fqn.split(".")[-1] if fqn else None)
            if resolved_name:
                return [
                    MetadataMetricReference(
                        name=resolved_name,
                        display_name=display_name,
                        fully_qualified_name=fqn,
                        description=description,
                    )
                ]
        if isinstance(value, str) and value.strip():
            clean = value.strip()
            return [MetadataMetricReference(name=clean.split(".")[-1], fully_qualified_name=clean)]
        return []

    @classmethod
    def _unique_entity_refs(
        cls,
        refs: list[MetadataEntityReference],
    ) -> list[MetadataEntityReference]:
        unique: list[MetadataEntityReference] = []
        seen: set[tuple[str | None, str]] = set()
        for ref in refs:
            identity = (ref.type, ref.fully_qualified_name or ref.name)
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(ref)
        return unique

    @staticmethod
    def entity_ref_payload(ref: MetadataEntityReference) -> dict[str, str | None]:
        return {
            "name": ref.name,
            "type": ref.type,
            "display_name": ref.display_name,
            "fully_qualified_name": ref.fully_qualified_name,
        }

    @staticmethod
    def metric_ref_payload(ref: MetadataMetricReference) -> dict[str, str | None]:
        return {
            "name": ref.name,
            "display_name": ref.display_name,
            "fully_qualified_name": ref.fully_qualified_name,
            "description": ref.description,
        }

    @staticmethod
    def extract_tag_fqns(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        tags: list[str] = []
        for item in value:
            if isinstance(item, dict):
                tag_fqn = item.get("tagFQN")
                if isinstance(tag_fqn, str) and tag_fqn.strip():
                    tags.append(tag_fqn.strip())
                    continue
                tag = item.get("tag")
                if isinstance(tag, dict):
                    nested_fqn = tag.get("fullyQualifiedName") or tag.get("name")
                    if isinstance(nested_fqn, str) and nested_fqn.strip():
                        tags.append(nested_fqn.strip())
                        continue
            elif isinstance(item, str) and item.strip():
                tags.append(item.strip())
        return tags

    @classmethod
    def extract_tier(cls, tags: list[str]) -> str | None:
        for tag in tags:
            normalized = tag.strip().lower()
            if normalized.startswith("tier."):
                return tag.strip()
        return None

    @classmethod
    def extract_certification(cls, tags: list[str]) -> str | None:
        for tag in tags:
            normalized = tag.strip().lower()
            if normalized.startswith("certification."):
                return tag.strip()
            if normalized in {"certified", "governance.certified"}:
                return tag.strip()
        return None

    @classmethod
    def extract_tier_from_payload(cls, value: Any, tags: list[str]) -> str | None:
        direct = cls._governance_value(value)
        if direct:
            return direct if direct.lower().startswith("tier.") else f"Tier.{direct}"
        return cls.extract_tier(tags)

    @classmethod
    def extract_certification_from_payload(cls, value: Any, tags: list[str]) -> str | None:
        direct = cls._governance_value(value)
        if direct:
            lowered = direct.lower()
            if lowered.startswith("certification.") or lowered in {"certified", "governance.certified"}:
                return direct
            return f"Certification.{direct}"
        return cls.extract_certification(tags)

    @classmethod
    def _governance_value(cls, value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            tag_label = value.get("tagLabel")
            nested = cls._governance_value(tag_label)
            if nested:
                return nested
            for key in ("tagFQN", "fullyQualifiedName", "displayName", "name"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        return None

    @classmethod
    def extract_usage_count(cls, value: Any) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int | float):
            return max(0, int(value))
        if isinstance(value, list):
            return max((cls.extract_usage_count(item) for item in value), default=0)
        if not isinstance(value, dict):
            return 0

        counts: list[int] = []
        for key, nested in value.items():
            lowered = key.lower()
            nested_count = cls.extract_usage_count(nested)
            if (
                lowered
                in {
                    "count",
                    "querycount",
                    "query_count",
                    "total",
                    "totalcount",
                    "total_count",
                }
                and nested_count > 0
            ) or (
                lowered in {"dailystats", "weeklystats", "monthlystats", "stats"}
                and nested_count > 0
            ):
                counts.append(nested_count)
        return max(counts, default=0)

    @classmethod
    def extract_quality_summary(cls, value: Any) -> DataQualitySummary | None:
        if not isinstance(value, dict):
            return None
        if "total" in value or "status" in value:
            failed_tests = value.get("failed_tests")
            normalized_failed_tests = (
                [
                    str(item).strip()
                    for item in failed_tests[:10]
                    if str(item).strip()
                ]
                if isinstance(failed_tests, list)
                else []
            )
            return DataQualitySummary(
                total=cls._safe_int(value.get("total")),
                success=cls._safe_int(value.get("success")),
                failed=cls._safe_int(value.get("failed")),
                aborted=cls._safe_int(value.get("aborted")),
                last_status=cls.to_optional_str(value.get("last_status") or value.get("status")),
                last_run_at=cls._safe_int(value.get("last_run_at")) or None,
                failed_tests=tuple(normalized_failed_tests),
            )
        data = value.get("data")
        if not isinstance(data, list):
            data = value.get("testCases")
        if not isinstance(data, list):
            data = value.get("tests")
        if not isinstance(data, list):
            return None

        success = failed = aborted = 0
        last_status: str | None = None
        last_run_at: int | None = None
        failed_tests: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            result = item.get("testCaseResult")
            if not isinstance(result, dict):
                result = item.get("latestResult")
            status = cls.to_optional_str(
                (result or {}).get("testCaseStatus")
                if isinstance(result, dict)
                else item.get("testCaseStatus")
            )
            normalized = (status or "").strip().lower()
            if normalized == "success":
                success += 1
            elif normalized == "failed":
                failed += 1
                failed_tests.append(
                    cls.to_optional_str(item.get("fullyQualifiedName"))
                    or cls.to_optional_str(item.get("name"))
                    or "unknown_test"
                )
            elif normalized == "aborted":
                aborted += 1
                failed_tests.append(
                    cls.to_optional_str(item.get("fullyQualifiedName"))
                    or cls.to_optional_str(item.get("name"))
                    or "unknown_test"
                )

            timestamp = None
            if isinstance(result, dict):
                raw_timestamp = result.get("timestamp")
                if isinstance(raw_timestamp, int | float) and not isinstance(raw_timestamp, bool):
                    timestamp = int(raw_timestamp)
            if timestamp is not None and (last_run_at is None or timestamp > last_run_at):
                last_run_at = timestamp
                last_status = status

        total = len([item for item in data if isinstance(item, dict)])
        if total == 0:
            return None
        if last_status is None:
            last_status = "Unknown"
        return DataQualitySummary(
            total=total,
            success=success,
            failed=failed,
            aborted=aborted,
            last_status=last_status,
            last_run_at=last_run_at,
            failed_tests=tuple(failed_tests[:10]),
        )

    @staticmethod
    def _safe_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int | float):
            return max(0, int(value))
        return 0

    @staticmethod
    def extract_search_metadata_results(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            raw_results = payload.get("results")
            if isinstance(raw_results, list):
                return [item for item in raw_results if isinstance(item, dict)]
            if raw_results is None and "data" in payload:
                data = payload.get("data")
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
        if isinstance(payload, list):
            results: list[dict[str, Any]] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                nested_results = item.get("results")
                if isinstance(nested_results, list):
                    results.extend(row for row in nested_results if isinstance(row, dict))
                if "fullyQualifiedName" in item or "name" in item or "displayName" in item:
                    results.append(item)
            return results
        return []

    @classmethod
    def normalize_table_entity_details(
        cls,
        payload: Any,
        *,
        normalize_entity_type: Any,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None

        entity_type = normalize_entity_type(payload.get("entityType") or payload.get("type"))
        fqn = cls.to_optional_str(
            payload.get("fullyQualifiedName") or payload.get("fqn") or payload.get("name")
        )
        raw_columns = payload.get("columns")
        if entity_type != "table" or not fqn or not isinstance(raw_columns, list):
            return None

        columns: list[dict[str, Any]] = []
        column_names: list[str] = []
        for column in raw_columns:
            if not isinstance(column, dict):
                continue
            name = cls.to_optional_str(column.get("name"))
            if not name:
                continue
            column_names.append(name)
            normalized_column: dict[str, Any] = {"name": name}
            data_type = cls.to_optional_str(
                column.get("dataTypeDisplay") or column.get("dataType") or column.get("type")
            )
            if data_type:
                normalized_column["dataType"] = data_type
            description = cls.to_optional_str(column.get("description"))
            if description:
                normalized_column["description"] = description
            tags = cls.extract_tag_fqns(column.get("tags"))
            if tags:
                normalized_column["tags"] = tags[:10]
            columns.append(normalized_column)

        tags = cls.extract_tag_fqns(payload.get("tags"))
        usage_count = cls.extract_usage_count(payload.get("usageSummary"))
        normalized: dict[str, Any] = {
            "entityType": "table",
            "name": cls.to_optional_str(payload.get("name")) or fqn.split(".")[-1],
            "displayName": cls.to_optional_str(payload.get("displayName"))
            or cls.to_optional_str(payload.get("name"))
            or fqn.split(".")[-1],
            "fullyQualifiedName": fqn,
            "description": cls.to_optional_str(payload.get("description")),
            "tableType": cls.to_optional_str(payload.get("tableType")),
            "service": cls.extract_nested_name(payload.get("service"))
            or cls.extract_nested_fqn(payload, "database", 0),
            "database": cls.extract_nested_name(payload.get("database"))
            or cls.extract_nested_fqn(payload, "database", 1),
            "databaseSchema": cls.extract_nested_name(payload.get("databaseSchema"))
            or cls.extract_nested_fqn(payload, "databaseSchema", -1),
            "columns": columns,
            "columnNames": column_names,
            "owners": cls.extract_entity_names(payload.get("owners"))[:10],
            "owner_teams": cls.extract_owner_teams(payload.get("owners"))[:10],
            "owner_refs": [
                cls.entity_ref_payload(ref)
                for ref in cls.extract_entity_refs(payload.get("owners"))[:10]
            ],
            "tags": tags[:20],
            "domains": cls.extract_entity_names(payload.get("domains") or payload.get("domain"))[
                :10
            ],
            "domain_refs": [
                cls.entity_ref_payload(ref)
                for ref in cls.extract_entity_refs(payload.get("domains") or payload.get("domain"))[
                    :10
                ]
            ],
            "glossary_terms": [
                cls.entity_ref_payload(ref)
                for ref in cls.extract_glossary_terms(payload)[:10]
            ],
            "related_metrics": [
                cls.metric_ref_payload(ref)
                for ref in cls.extract_metric_refs(
                    payload.get("relatedMetrics") or payload.get("metrics")
                )[:10]
            ],
            "tier": cls.extract_tier_from_payload(payload.get("tier"), tags),
            "certification": cls.extract_certification_from_payload(
                payload.get("certification"),
                tags,
            ),
        }
        quality_summary = cls.extract_quality_summary(payload.get("quality_summary"))
        if quality_summary is not None:
            normalized["quality_summary"] = quality_summary.to_payload()
        if usage_count > 0:
            normalized["usage_count"] = usage_count
        return {key: value for key, value in normalized.items() if value not in (None, [], {})}

    @staticmethod
    def extract_nested_fqn(
        item: dict[str, Any],
        key: str,
        position: int,
    ) -> str | None:
        value = item.get(key)
        if not isinstance(value, dict):
            return None
        fqn = value.get("fullyQualifiedName")
        if not isinstance(fqn, str):
            return None
        chunks = fqn.split(".")
        if not chunks:
            return None
        if position == -1:
            return chunks[-1]
        if position < 0 or position >= len(chunks):
            return None
        return chunks[position]

    @classmethod
    def table_context_from_any_payload(
        cls,
        value: Any,
        *,
        normalize_entity_type: Any,
    ) -> TableContext | None:
        if not isinstance(value, dict):
            return None
        entity_type = normalize_entity_type(value.get("entityType") or value.get("type"))
        fqn = cls.to_optional_str(
            value.get("fullyQualifiedName") or value.get("fqn") or value.get("name")
        )
        if not fqn:
            return None
        is_table_like = entity_type == "table" or any(
            key in value for key in ("columns", "columnNames", "databaseSchema")
        )
        if not is_table_like:
            return None

        column_names = value.get("columnNames")
        raw_columns = value.get("columns")
        columns: list[str] = []
        if isinstance(column_names, list):
            columns = [
                str(column).strip()
                for column in column_names[:20]
                if str(column).strip()
            ]
        elif isinstance(raw_columns, list):
            for column in raw_columns[:20]:
                if not isinstance(column, dict):
                    continue
                name = cls.to_optional_str(column.get("name"))
                if name:
                    columns.append(name)

        tags = cls.extract_tag_fqns(value.get("tags"))
        owner_refs = cls.extract_entity_refs(value.get("owners"))
        domain_refs = cls.extract_entity_refs(value.get("domains") or value.get("domain"))
        return TableContext(
            source="mcp",
            fqn=fqn,
            columns=columns,
            description=cls.to_optional_str(value.get("description")),
            service=cls.extract_nested_name(value.get("service"))
            or cls.to_optional_str(value.get("service")),
            database=cls.extract_nested_name(value.get("database"))
            or cls.to_optional_str(value.get("database")),
            schema=cls.extract_nested_name(value.get("databaseSchema"))
            or cls.extract_nested_name(value.get("schema"))
            or cls.to_optional_str(value.get("schema")),
            owners=cls.extract_entity_names(value.get("owners")),
            owner_refs=owner_refs,
            owner_teams=[
                ref.name
                for ref in owner_refs
                if (ref.type or "").strip().lower() == "team"
            ],
            tags=tags,
            domains=cls.extract_entity_names(value.get("domains") or value.get("domain")),
            domain_refs=domain_refs,
            glossary_terms=cls.extract_glossary_terms(value),
            related_metrics=cls.extract_metric_refs(
                value.get("relatedMetrics") or value.get("metrics")
            ),
            quality_summary=cls.extract_quality_summary(value.get("quality_summary")),
            tier=cls.extract_tier_from_payload(value.get("tier"), tags),
            certification=cls.extract_certification_from_payload(
                value.get("certification"),
                tags,
            ),
            usage_count=cls.extract_usage_count(value.get("usageSummary")),
        )

    @classmethod
    def extract_related_table_contexts(
        cls,
        payload: Any,
        *,
        normalize_entity_type: Any,
    ) -> list[TableContext]:
        found: dict[str, TableContext] = {}

        def _walk(value: Any) -> None:
            table = cls.table_context_from_any_payload(
                value,
                normalize_entity_type=normalize_entity_type,
            )
            if table is not None:
                found.setdefault(table.fqn, table)
            if isinstance(value, dict):
                for nested in value.values():
                    _walk(nested)
            elif isinstance(value, list):
                for item in value:
                    _walk(item)

        _walk(payload)
        return list(found.values())

    @classmethod
    def catalog_item_from_table(cls, table: TableContext) -> dict[str, Any]:
        return {
            "entityType": "table",
            "fullyQualifiedName": table.fqn,
            "displayName": table.fqn.split(".")[-1],
            "description": table.description,
            "service": table.service,
            "database": table.database,
            "databaseSchema": table.schema,
            "columnNames": table.columns[:20],
            "owners": table.owners[:10],
            "owner_refs": [cls.entity_ref_payload(ref) for ref in table.owner_refs[:10]],
            "owner_teams": table.owner_teams[:10],
            "tags": table.tags[:10],
            "domains": table.domains[:10],
            "domain_refs": [cls.entity_ref_payload(ref) for ref in table.domain_refs[:10]],
            "glossary_terms": [
                cls.entity_ref_payload(ref) for ref in table.glossary_terms[:10]
            ],
            "related_metrics": [
                cls.metric_ref_payload(ref) for ref in table.related_metrics[:10]
            ],
            "quality_summary": (
                table.quality_summary.to_payload()
                if table.quality_summary is not None
                else None
            ),
            "usageSummary": {"count": table.usage_count} if table.usage_count > 0 else {},
        }
