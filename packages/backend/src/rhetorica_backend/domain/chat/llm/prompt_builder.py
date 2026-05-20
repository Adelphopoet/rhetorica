"""Prompt builder for system prompts and tool definitions."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, ClassVar

from rhetorica_backend.domain.chat.metadata import MetadataSemanticGroundingService
from rhetorica_backend.domain.chat.sql_prompt_window import (
    DEFAULT_SQL_PROMPT_ROWS,
    MAX_SQL_PROMPT_ROWS,
)


class PromptBuilder:
    """Builds system prompts and tool definitions for LLM requests."""

    PROMPT_VERSION = "v2026.05.10b"
    _FINAL_RESPONSE_MINIMAL_EXAMPLE: ClassVar[str] = json.dumps(
        {
            "schema_version": "v1",
            "answer_markdown": "Привет! Чем могу помочь?",
            "sql": None,
            "target": None,
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [],
            "clarification_request": None,
            "analysis_document": None,
            "visualization": None,
        },
        ensure_ascii=False,
        indent=2,
    )

    @staticmethod
    def get_prompt_version() -> str:
        """Return current prompt contract version."""
        return PromptBuilder.PROMPT_VERSION

    @staticmethod
    def build_system_prompt(
        run_sql_allowed: bool,
        metadata_configured: bool,
        has_db_connectors: bool,
        rag_configured: bool = False,
        confluence_configured: bool = False,
        jira_read_configured: bool = False,
        jira_write_configured: bool = False,
        profile_tools_allowed: bool = False,
        prompt_mode: str | None = None,
        workspace_skill_guidance: str | None = None,
        current_datetime: datetime | None = None,
    ) -> str:
        """
        Build system prompt for analytics SQL assistant.

        Args:
            run_sql_allowed: Whether SQL execution is allowed.
            metadata_configured: Whether metadata connector is configured.
            has_db_connectors: Whether database connectors are available.

        Returns:
            System prompt string.
        """
        resolved_prompt_mode = prompt_mode or PromptBuilder._default_prompt_mode(
            run_sql_allowed=run_sql_allowed,
            metadata_configured=metadata_configured,
            rag_configured=rag_configured,
            confluence_configured=confluence_configured,
            jira_read_configured=jira_read_configured,
            jira_write_configured=jira_write_configured,
            profile_tools_allowed=profile_tools_allowed,
        )
        tools_available = (
            metadata_configured
            or rag_configured
            or confluence_configured
            or jira_read_configured
            or jira_write_configured
            or profile_tools_allowed
            or (run_sql_allowed and has_db_connectors)
        )
        time_context = PromptBuilder._build_time_context(current_datetime)
        if tools_available:
            common_contract = (
                f"prompt_version={PromptBuilder.PROMPT_VERSION}\n"
                "You are an analytics assistant.\n"
                f"{time_context}"
                f"Current turn mode is {resolved_prompt_mode}.\n"
                "Contract:\n"
                "- Use native function tools when runtime data, metadata, docs, Jira, or profiling context is needed.\n"
                "- Final output must be only ChatFinalResponseSchema v1 JSON: no prose outside JSON, markdown fences, <think>, reasoning traces, progress, plans, status updates, or hidden tool tags.\n"
                "- After a tool result, call the next needed tool or return final JSON.\n"
                "- Never call the same tool twice with identical arguments.\n"
                "- For useful charts after run_sql, call build_visualization and put the validated VisualizationSpec v2 in final visualization; otherwise set visualization=null.\n"
                "- Daily, weekly, or monthly metric-by-date SQL results are visual answers unless the user asked for table only.\n"
                "- Never render charts as ASCII/Unicode/fenced text diagrams in answer_markdown.\n"
                "- Answer casual or broad conceptual turns directly unless workspace data/docs are needed.\n"
                "- If a tool is unavailable, do not call it.\n"
            )
            jira_rules = (
                "- Use Jira read tools to search issues, inspect issue bodies, and infer delivery ownership when the question references tickets, incidents, complaints, or feature requests.\n"
                "- Jira read tools may be used in non-analytic turns when issue context is needed.\n"
                "- search_jira_issues supports issue lookup and routing discovery. Use get_jira_issue when the exact key is known or when full body/details are needed.\n"
                "- create_jira_issue is a write tool. Use it when the conversation context shows the user wants a Jira issue to be created or registered.\n"
                "- If create_jira_issue returns that project or assignee is unclear, ask the user for the missing routing data instead of inventing one.\n"
            )
            discovery_rules = (
                "- Use metadata tools for discovery and definitions.\n"
                "- Metadata connector_name means a Metadata connector, not a Database connector.\n"
                "- In follow-up turns, reuse the most recent governed table and connector from context unless the user explicitly changes dataset/source.\n"
                "- Do not switch to a different table on a follow-up just because another candidate has a vaguely matching column.\n"
                "- For exact table name, prefix, or schema lookup, use fetch_metadata_tables first.\n"
                "- Treat fetch_metadata_tables/search_metadata_catalog column lists as compact previews; wide tables can be truncated. Use get_metadata_entity_details for the selected table before SQL planning against columns not fully proven.\n"
                "- For short keyword lookup like `price`, `orders`, or `dau`, prefer table-first metadata lookup.\n"
                "- Do not concatenate translations or loose synonyms into one metadata search query; use the strongest token or separate lookups.\n"
                "- OpenMetadata search syntax: use `*term*` for substring, uppercase `AND/OR/NOT` for boolean logic, and `field:value` for explicit field filters.\n"
                "- If the user names a specific table and asks about a specific column, join key, or schema detail, do not answer from a broad candidate list alone. Identify the exact table, then call get_metadata_entity_details for that table before answering.\n"
                "- Do not answer a column-existence question from a partial column preview when the table is known.\n"
                "- For 'what is <table>' or 'что такое <table>' questions, once metadata identifies matching tables, answer directly from metadata. Do not end with progress text or promise another search.\n"
                "- Search RAG or Confluence only if metadata is insufficient to answer the definition question.\n"
                "- For broad semantic discovery when the name is unknown, use search_metadata_catalog.\n"
                "- In broad discovery, search_metadata_catalog may surface glossary terms and metrics; use their linked tables when they point to a stronger governed source.\n"
                "- For entity lookup by exact name, prefix, or pattern, prefer metadata tools first and use run_sql only as a last fallback.\n"
                "- If a prefix or pattern lookup returns multiple matching tables, report the candidate set or count. Do not present one arbitrary table as the only match unless there is an exact leaf-name match.\n"
                "- When metadata results include relevance_score, matched_on, certification, tier, domains, owners, or usage_count, use them to judge table quality instead of guessing.\n"
                "- Prefer governed tables with certification, tier, owners, domain, descriptions, and strong schema/name/column matches.\n"
                "- Prefer business-layer marts like dm over technical layers like stg/ods/dds unless the user explicitly asked for technical objects.\n"
                "- For generic business questions, prefer canonical tables over source-specific variants like *_bitrix, *_sap, *_tmp when a broader governed table exists.\n"
                "- Treat sandbox/test/tmp/dev/mock/sample/playground objects as suspicious defaults unless the user explicitly asked for them.\n"
                "- If the candidate set is weak, low-score, or mostly suspicious, say so and present candidates. Do not pretend one garbage table is the right answer.\n"
                "- If semantic grounding or metadata ranking already identifies a strong governed candidate, do not replace it in the answer with a weaker runtime fallback just because runtime returned a similarly named sandbox/stage table.\n"
            )
            sql_rules = (
                "- Never generate destructive or mutating SQL, even as an example or draft. Refuse DROP/DELETE/TRUNCATE/ALTER/INSERT/UPDATE/CREATE/GRANT/REVOKE style requests and offer a safe read-only alternative.\n"
                "- Use run_sql only for real metrics, numbers, trends, rankings, or explicit execution requests.\n"
                "- For definition/entity questions about what a table or entity is, do not use run_sql.\n"
                "- Explicit SQL inspection commands like DESCRIBE, DESC, SHOW CREATE, EXISTS, and EXPLAIN are execution requests; use run_sql when asked to run them.\n"
                "- Respect connector engine syntax. Generate Trino SQL for Trino connectors and ClickHouse SQL for ClickHouse connectors.\n"
                "- Never use metadata service prefixes like service.catalog.schema.table in executable SQL. For Trino use catalog.schema.table. For ClickHouse use schema.table.\n"
                "- For Trino connectors, never use ClickHouse-only syntax like system.tables, system.columns, arrayJoin, uniqExact, toDate, toDateTime, PREWHERE, or LIMIT BY.\n"
                "- For Trino schema inspection, prefer SHOW COLUMNS, DESCRIBE, or information_schema.columns.\n"
                "- Do not use SHOW CREATE TABLE on Trino connectors; treat it as an unsafe default and choose a read-only schema inspection query instead.\n"
                "- Use run_sql only with connector names from the Database connectors list in the user message.\n"
                "- Metadata may mention tables from unconnected sources; do not execute SQL there. Find an equivalent in connected sources or explain that execution is unavailable.\n"
                "- Complex analytical SQL may use JOIN, CTE, and subqueries when needed.\n"
                "- Before multi-table SQL, ground every table and join key with metadata tools unless they are already grounded in recent context.\n"
                "- For multi-table SQL, keep all referenced tables inside one connector map / execution zone. Never join tables from different metadata databases or different connector maps.\n"
                "- If candidate tables span multiple connector maps or metadata databases, narrow the plan to one executable source before calling run_sql.\n"
                "- For analytical SQL requests, ground the target table and exact columns before the first run_sql whenever schema details are missing or uncertain.\n"
                "- When metadata or user context lists table/column names, copy identifiers exactly. Never invent near-match names like created vs create.\n"
                "- When grounded schema hints list columns, use only those exact column names in SQL. Do not invent aliases as grouping keys and do not swap ptn_dt/date/event_date or customer_id/user_id/contact_id unless that exact column is present.\n"
                "- If the user asked for daily data, group by day-level date values. Do not aggregate to month unless the user explicitly asked for month-level output.\n"
                "- If run_sql returns sql_preflight_failed, inspect preflight_errors, fix the SQL or connector choice, and retry once with changed arguments.\n"
                "- If preflight_errors include similar columns, prefer those exact suggestions on retry.\n"
                "- If run_sql returns sql_preflight_uncertain, hydrate metadata first with fetch_metadata_tables or search_metadata_catalog, then retry run_sql once.\n"
                "- If preflight blockers remain unclear, do not repeat the same run_sql call. Explain the blocker or ask one clarification question.\n"
                "- If run_sql returns optimizer_summary or optimizer_hints, inspect them before deciding on another query.\n"
                "- Do not ignore optimizer findings. If they suggest a safe filter mirror, predicate pushdown, or similar SQL-local improvement, revise the next run_sql instead of repeating near-identical SQL.\n"
                "- When the final answer relies on a query with applied optimizer rewrites or important optimizer findings, mention that briefly in the answer.\n"
                "- If a successful run_sql already answers the question, stop and finalize. Do not run min/max probes, sample queries, or alternate-table checks unless the result is empty, contradictory, or obviously unusable.\n"
                "- If multiple SQL executions were attempted, base the final answer on the execution result that best supports the answer. A later failed attempt does not invalidate an earlier successful one by itself.\n"
                "- When you choose among multiple successful executions, include execution_result evidence referencing the chosen execution_id.\n"
                "- For root-cause, decomposition, complex, or table-proof questions, follow the active plan artifact (analysis_plan) and close required evidence before finalizing.\n"
                "- If the user asks to prove the answer with tables, the final answer must include a markdown table with numbers from executed SQL and execution_result evidence.\n"
                f"- run_sql returns a bounded prompt window, not a full result export. Default prompt_row_limit is {DEFAULT_SQL_PROMPT_ROWS}; maximum is {MAX_SQL_PROMPT_ROWS}.\n"
                "- Request a higher prompt_row_limit when detailed row-level proof is required, but keep SQL result shape relevant to the user question.\n"
                "- Do not try to get more prompt rows by repeating the same query, formatting numbers, or packing many rows into arrays/strings.\n"
                "- If rows_truncated is true and the window is insufficient, use one of these explicit paths: rerun with a higher prompt_row_limit, write an answer-ready aggregate SQL query, or page deterministically with stable ORDER BY plus LIMIT/OFFSET.\n"
                "- For table-proof answers, prefer compact proof tables: aggregate comparisons, top/bottom drivers, or one-row summary metrics instead of long row listings.\n"
            )
            profile_rules = (
                "- Use profile_table for bounded table shape/freshness/null/cardinality checks when the user asks to inspect data health or table profile.\n"
                "- Use profile_metric only when a metric and its underlying table are both known.\n"
                "- Do not use profile tools for full data retrieval; use run_sql for answer-ready analytical results.\n"
            )
            prompt = common_contract
            if jira_read_configured or jira_write_configured:
                prompt += jira_rules
            if resolved_prompt_mode in {"discovery", "sql_generation"}:
                prompt += discovery_rules
            if profile_tools_allowed:
                prompt += profile_rules
            if resolved_prompt_mode == "sql_generation":
                prompt += sql_rules
                if workspace_skill_guidance:
                    prompt += workspace_skill_guidance.rstrip() + "\n"
            prompt += (
                f"metadata_tool_available={str(metadata_configured).lower()}.\n"
                f"rag_tool_available={str(rag_configured).lower()}.\n"
                f"confluence_tool_available={str(confluence_configured).lower()}.\n"
                f"jira_read_tool_available={str(jira_read_configured).lower()}.\n"
                f"jira_write_tool_available={str(jira_write_configured).lower()}.\n"
                f"profile_tools_available={str(profile_tools_allowed).lower()}.\n"
                f"run_sql_tool_available={str(run_sql_allowed and has_db_connectors).lower()}.\n"
                "Minimal valid final JSON for direct answers:\n"
                f"{PromptBuilder._FINAL_RESPONSE_MINIMAL_EXAMPLE}\n"
            )
            return prompt

        return (
            f"prompt_version={PromptBuilder.PROMPT_VERSION}\n"  # noqa: S608
            "You are an analytics assistant.\n"
            f"{time_context}"
            "Return ONLY a valid JSON object for ChatFinalResponseSchema v1.\n"
            "No markdown code fences, <think>, reasoning traces, or prose outside JSON.\n"
            "answer_markdown must be the final user-facing answer.\n"
            "sql must be null when SQL is not required.\n"
            "When sql is present it must be one read-only SELECT statement.\n"
            "Answer casual or broad conceptual turns directly when no tools are needed.\n"
            "Never generate destructive or mutating SQL, even as draft/example text.\n"
            "target may include connector_name, service, database, schema.\n"
            "claims must contain atomic statements from answer_markdown.\n"
            "Each claim must be linked to evidence entries via claim_evidence_links.\n"
            "Allowed evidence types: execution_result, metadata_entity, rag_insight, confluence_page.\n"
            'If clarification is required, set uncertainty_flags to include "needs_user_clarification" and ask one concise question in answer_markdown.\n'
            "Minimal valid final JSON for direct answers:\n"
            f"{PromptBuilder._FINAL_RESPONSE_MINIMAL_EXAMPLE}\n"
        )

    @staticmethod
    def _build_time_context(current_datetime: datetime | None = None) -> str:
        resolved = current_datetime or datetime.now().astimezone()
        if resolved.tzinfo is None:
            resolved = resolved.astimezone()
        timezone = resolved.tzname() or resolved.strftime("UTC%z")
        return (
            "Current date/time context:\n"
            f"- current_datetime={resolved.isoformat()}\n"
            f"- current_weekday={resolved.strftime('%A')}\n"
            f"- current_timezone={timezone}\n"
            "- Resolve relative dates like today, yesterday, this week, and this month from this context.\n"
        )

    @staticmethod
    def build_finalizer_system_prompt() -> str:
        """Build system prompt for the no-tools finalizer step."""
        return (
            f"prompt_version={PromptBuilder.PROMPT_VERSION}\n"
            "You are the finalizer for analytics chat orchestration.\n"
            "Return ONLY a valid JSON object for ChatFinalResponseSchema v1.\n"
            "No markdown fences, prose outside JSON, or tool calls.\n"
            "You receive raw final assistant text plus grounded runtime context from the orchestrator.\n"
            "Produce the final user-facing answer in answer_markdown.\n"
            "Preserve the markdown structure and line breaks from raw_final_text when it is already a usable answer.\n"
            "Do not collapse tables, lists, headings, blockquotes, or code fences into one paragraph.\n"
            "Use execution, SQL, metadata, and allowed evidence references from context when they help.\n"
            "Visualization is model-owned: return null or an explicit spec in the visualization field; do not rely on implicit chart construction.\n"
            "Keep answer_markdown textual. Replace any ASCII/Unicode chart diagrams with plain text. "
            "Put any real chart spec only in the visualization field.\n"
            "Never render charts as ASCII art, Unicode bar blocks, or fenced text diagrams inside answer_markdown.\n"
            "If context contains visualization_candidate, copy it only when it is already valid for the chosen execution; otherwise set visualization=null.\n"
            "If semantic_grounding shows metadata materially influenced source selection, add 1-2 short sentences explaining the chosen table.\n"
            "When semantic_grounding contains a non-suspicious chosen table with positive relevance_score, treat it as the authoritative source choice for answer_markdown.\n"
            "If raw_final_text claims the table was not found or leads with suspicious runtime fallback tables while semantic_grounding has a strong governed match, rewrite the answer to center the governed match instead.\n"
            "Mention runtime fallback tables only as weak alternatives or caveats when they conflict with a stronger governed semantic choice.\n"
            "If semantic_grounding shows a weak or conflicting match, say that explicitly and mention the alternative candidates.\n"
            "Do not add semantic rationale for casual or non-data turns when semantic_grounding is absent.\n"
            "Use only evidence references listed in allowed_evidence_references.\n"
            "Use analysis_plan, worker_result, and checkpoint_digest artifacts only in their compact envelope form; never ask for or reconstruct raw worker transcripts.\n"
            "For table-proof requests, include a markdown table with numbers from executed SQL. If executed SQL evidence is missing, state the missing evidence instead of pretending proof exists.\n"
            "If claims, evidence, or claim_evidence_links are unavailable, return empty arrays.\n"
            'If clarification is truly required, include "needs_user_clarification" in uncertainty_flags and ask one concise question.\n'
        )

    @staticmethod
    def build_finalizer_user_prompt(context: dict[str, Any]) -> str:
        """Build user prompt payload for the no-tools finalizer step."""
        return (
            "Finalize this assistant turn into ChatFinalResponseSchema v1.\n"
            "Context JSON:\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)}"
        )

    @staticmethod
    def build_finalizer_repair_system_prompt() -> str:
        """Build system prompt for repairing invalid finalizer output."""
        return (
            f"prompt_version={PromptBuilder.PROMPT_VERSION}\n"
            "You repair invalid finalizer output.\n"
            "Return ONLY a valid JSON object for ChatFinalResponseSchema v1.\n"
            "No markdown, prose, tool calls, <think>, or reasoning traces outside JSON.\n"
            "Keep the answer grounded in the provided raw final assistant text and state context.\n"
            "Preserve the markdown structure and line breaks from raw_final_text when they are already valid.\n"
            "Preserve semantic selection rationale when semantic_grounding shows metadata materially influenced source selection.\n"
            "Rebuild visualization explicitly: set `visualization` to null or to a valid VisualizationSpec v2 object.\n"
            "Never keep or recreate ASCII art, Unicode bar blocks, or fenced text charts in answer_markdown.\n"
            "Use only allowed evidence references from context.\n"
            "Minimal valid direct-answer JSON:\n"
            f"{PromptBuilder._FINAL_RESPONSE_MINIMAL_EXAMPLE}\n"
        )

    @staticmethod
    def build_finalizer_repair_user_prompt(context: dict[str, Any]) -> str:
        """Build user prompt payload for finalizer repair."""
        return (
            "Repair the invalid finalizer output below.\n"
            "Repair context JSON:\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)}"
        )

    @staticmethod
    def build_tools(
        run_sql_allowed: bool,
        metadata_configured: bool,
        has_db_connectors: bool,
        rag_configured: bool = False,
        confluence_configured: bool = False,
        jira_read_configured: bool = False,
        jira_write_configured: bool = False,
        profile_tools_allowed: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Build tool definitions for LLM request.

        Args:
            run_sql_allowed: Whether SQL execution is allowed.
            metadata_configured: Whether metadata connector is configured.
            has_db_connectors: Whether database connectors are available.

        Returns:
            List of tool definitions.
        """
        tool_names: list[str] = []
        if metadata_configured:
            tool_names.extend([
                "fetch_metadata_tables",
                "search_metadata_catalog",
                "get_metadata_entity_details",
                "get_metadata_entity_lineage",
            ])
        if run_sql_allowed and has_db_connectors:
            tool_names.append("run_sql")
            tool_names.append("build_visualization")
        if profile_tools_allowed and has_db_connectors:
            tool_names.extend(["profile_table", "profile_metric"])
        if rag_configured:
            tool_names.append("search_rag_insights")
        if confluence_configured:
            tool_names.append("search_confluence_pages")
        if jira_read_configured:
            tool_names.extend(["search_jira_issues", "get_jira_issue"])
        if jira_write_configured:
            tool_names.append("create_jira_issue")
        return PromptBuilder.build_tools_from_names(tool_names)

    @staticmethod
    def build_tools_from_names(tool_names: list[str]) -> list[dict[str, Any]]:
        """Build tool definitions from explicit registry-resolved tool names."""
        tools: list[dict[str, Any]] = []
        tool_name_set = set(tool_names)

        if any(
            name in tool_name_set
            for name in (
                "fetch_metadata_tables",
                "search_metadata_catalog",
                "get_metadata_entity_details",
                "get_metadata_entity_lineage",
            )
        ):
            tools.extend([
                {
                    "type": "function",
                    "function": {
                        "name": "fetch_metadata_tables",
                        "description": (
                            "Fetch table metadata from OpenMetadata and optional MCP bridge. "
                            "USE WHEN: you need exact table lookup, prefix lookup, schema discovery, or columns for SQL planning. "
                            "Search results may be compact and column lists may be truncated on wide tables; for the full authoritative schema of a selected table, call get_metadata_entity_details with that table FQN. "
                            "DO NOT USE: for direct data retrieval or SQL execution. "
                            "If the user asks about one specific column on one known table, use this to identify the table and then call get_metadata_entity_details before answering."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "connector_name": {
                                    "type": "string",
                                    "description": (
                                        "Metadata connector name only. "
                                        "Use a value from the Metadata connectors list, not from Database connectors."
                                    ),
                                },
                                "search": {"type": "string"},
                                "service": {"type": "string"},
                                "database": {"type": "string"},
                                "schema": {"type": "string"},
                                "limit": {
                                    "type": "integer",
                                    "default": 20,
                                    "minimum": 1,
                                    "maximum": 200,
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "search_metadata_catalog",
                        "description": (
                            "Search OpenMetadata catalog via MCP. "
                            "USE WHEN: user asks to find data assets/entities by meaning or business concept, including tables, teams, domains, glossary terms, metrics, and quality context. "
                            "For short keyword lookup, prefer entity_type=table and keep query to the strongest catalog term. "
                            "If schema/database/service are already known, pass them separately and keep query focused on the entity name. "
                            "Catalog search is discovery, not full schema inspection; after selecting a table, call get_metadata_entity_details before planning SQL against uncertain columns. "
                            "Tool results include discovery.has_more, discovery.evidence_gaps, and discovery.next_allowed_actions; use them to decide whether to inspect details, change offset/scope/query, run SQL, or ask clarification. "
                            "If a tool result has duplicate=true or new_evidence=false, do not repeat the same call; use existing evidence or change query/offset/scope. "
                            "DO NOT USE: for exact table name, prefix, or schema lookup when fetch_metadata_tables is enough."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "connector_name": {
                                    "type": "string",
                                    "description": (
                                        "Metadata connector name only. "
                                        "Use a value from the Metadata connectors list, not from Database connectors."
                                    ),
                                },
                                "query": {"type": "string"},
                                "entity_type": {"type": "string"},
                                "service": {"type": "string"},
                                "database": {"type": "string"},
                                "schema": {"type": "string"},
                                "limit": {
                                    "type": "integer",
                                    "default": 20,
                                    "minimum": 1,
                                    "maximum": 50,
                                },
                                "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
                            },
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "get_metadata_entity_details",
                        "description": (
                            "Get full metadata payload for one entity via MCP. "
                            "USE WHEN: details are needed for an already identified entity. "
                            "For entity_type=table, this is the primary way to inspect the complete table schema, owners/teams, domains, glossary terms, related metrics, and data quality before SQL planning. "
                            "Use this before answering exact column-existence, join-key, or schema-detail questions about a known table. "
                            "DO NOT USE: for broad discovery."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "connector_name": {
                                    "type": "string",
                                    "description": (
                                        "Metadata connector name only. "
                                        "Use a value from the Metadata connectors list, not from Database connectors."
                                    ),
                                },
                                "entity_type": {"type": "string"},
                                "fqn": {"type": "string"},
                            },
                            "required": ["entity_type", "fqn"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "get_metadata_entity_lineage",
                        "description": (
                            "Get upstream/downstream lineage via MCP. "
                            "USE WHEN: dependency/impact analysis is needed. "
                            "DO NOT USE: if question can be answered without lineage."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "connector_name": {
                                    "type": "string",
                                    "description": (
                                        "Metadata connector name only. "
                                        "Use a value from the Metadata connectors list, not from Database connectors."
                                    ),
                                },
                                "entity_type": {"type": "string"},
                                "fqn": {"type": "string"},
                                "upstream_depth": {"type": "integer", "minimum": 0, "maximum": 10},
                                "downstream_depth": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 10,
                                },
                            },
                            "required": ["entity_type", "fqn"],
                            "additionalProperties": False,
                        },
                    },
                },
            ])

        if "run_sql" in tool_name_set:
            tools.append({
                "type": "function",
                "function": {
                    "name": "run_sql",
                    "description": (
                        "Run read-only SQL through guarded execution path. "
                        "USE WHEN: user explicitly requests query execution or real data result. "
                        "For schema discovery, prefer metadata tools first; use SQL introspection only when metadata tools cannot identify the needed table or columns. "
                        "Returns a bounded prompt preview only; use SQL aggregation to make the preview answer-ready. "
                        "Use prompt_row_limit for larger but bounded row windows. "
                        "Set requires_approval=true before high-cost SQL, then wait for user approval and retry with approval_token. "
                        "DO NOT USE: greetings, drafting SQL, or metadata-only questions."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "sql": {"type": "string"},
                            "connector_name": {
                                "type": "string",
                                "description": (
                                    "Database connector name only. "
                                    "Use a value from the Database connectors list."
                                ),
                            },
                            "service": {"type": "string"},
                            "database": {"type": "string"},
                            "schema": {"type": "string"},
                            "requires_approval": {
                                "type": "boolean",
                                "description": (
                                    "Set true when this SQL should wait for explicit user approval before execution."
                                ),
                            },
                            "approval_reason": {
                                "type": "string",
                                "description": "Short user-facing reason for the approval gate.",
                            },
                            "approval_token": {
                                "type": "string",
                                "description": (
                                    "Token from checkpoint pending approval. Include it only after the user approves."
                                ),
                            },
                            "prompt_row_limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": MAX_SQL_PROMPT_ROWS,
                                "description": (
                                    "Maximum result rows to include in the LLM prompt window. "
                                    f"Default {DEFAULT_SQL_PROMPT_ROWS}; "
                                    "does not change SQL safety row limits."
                                ),
                            },
                        },
                        "required": ["sql"],
                        "additionalProperties": False,
                    },
                },
            })

        if "build_visualization" in tool_name_set:
            tools.append({
                "type": "function",
                "function": {
                    "name": "build_visualization",
                    "description": (
                        "Validate a model-chosen VisualizationSpec v2 against a successful run_sql execution preview. "
                        "USE WHEN: after run_sql, a chart would make the answer clearer. "
                        "You must choose the chart kind, execution_id, x field, y fields, and rationale explicitly. "
                        "This tool does not infer chart intent or pick fields for you. "
                        "DO NOT USE: before SQL execution, for pure text answers, or when no chart is useful."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "execution_id": {
                                "type": "string",
                                "description": "execution_id from the successful run_sql result to visualize.",
                            },
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "bar",
                                    "bar_grouped",
                                    "bar_stacked",
                                    "line",
                                    "area",
                                    "area_stacked",
                                    "combo_line_bar",
                                    "scatter",
                                    "bubble",
                                    "pie",
                                    "donut",
                                    "heatmap",
                                    "treemap",
                                    "funnel",
                                    "histogram",
                                    "waterfall",
                                ],
                            },
                            "goal": {
                                "type": "string",
                                "enum": [
                                    "inspection",
                                    "trend",
                                    "comparison",
                                    "composition",
                                    "correlation",
                                    "distribution",
                                    "ranking",
                                    "delta",
                                ],
                                "default": "inspection",
                            },
                            "title": {"type": "string"},
                            "summary": {"type": "string"},
                            "x": {"type": "string"},
                            "y": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                            "series": {"type": "string"},
                            "color": {"type": "string"},
                            "size": {"type": "string"},
                            "secondary_y": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "tooltip": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "sort": {"type": "string", "enum": ["asc", "desc"]},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 200,
                                "default": 50,
                            },
                            "aggregation": {
                                "type": "string",
                                "enum": ["none", "sum", "avg", "count"],
                                "default": "none",
                            },
                            "top_n": {"type": "integer", "minimum": 1, "maximum": 200},
                            "normalize": {
                                "type": "string",
                                "enum": ["none", "percent_of_total", "percent_of_series"],
                                "default": "none",
                            },
                            "cumulative": {"type": "boolean", "default": False},
                            "why_kind": {"type": "string"},
                            "why_fields": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["execution_id", "kind", "x", "y", "why_kind"],
                        "additionalProperties": False,
                    },
                },
            })

        if "profile_table" in tool_name_set:
            tools.append({
                "type": "function",
                "function": {
                    "name": "profile_table",
                    "description": (
                        "Return a bounded table profile: row count, freshness, nulls, "
                        "cardinality, and sample values. USE WHEN: user asks about table "
                        "health, shape, freshness, sparsity, or profiling. DO NOT USE: for "
                        "full data retrieval or answer-ready aggregates."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "table_name": {
                                "type": "string",
                                "description": (
                                    "Database table reference for the selected Database connector."
                                ),
                            },
                            "connector_name": {
                                "type": "string",
                                "description": (
                                    "Database connector name only. "
                                    "Use a value from the Database connectors list."
                                ),
                            },
                            "columns": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional columns to profile.",
                            },
                        },
                        "required": ["table_name"],
                        "additionalProperties": False,
                    },
                },
            })

        if "profile_metric" in tool_name_set:
            tools.append({
                "type": "function",
                "function": {
                    "name": "profile_metric",
                    "description": (
                        "Return a bounded profile for a known metric through its table. "
                        "USE WHEN: metric_name and table_name are known and the user asks "
                        "about metric health, freshness, sparsity, or distribution shape. "
                        "DO NOT USE: before the metric table is identified."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "metric_name": {"type": "string"},
                            "table_name": {
                                "type": "string",
                                "description": (
                                    "Underlying database table reference for the metric."
                                ),
                            },
                            "connector_name": {
                                "type": "string",
                                "description": (
                                    "Database connector name only. "
                                    "Use a value from the Database connectors list."
                                ),
                            },
                            "columns": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional metric/table columns to profile.",
                            },
                        },
                        "required": ["metric_name", "table_name"],
                        "additionalProperties": False,
                    },
                },
            })

        if "search_rag_insights" in tool_name_set:
            tools.append({
                "type": "function",
                "function": {
                    "name": "search_rag_insights",
                    "description": (
                        "Search reusable insights in RAG knowledge base. "
                        "USE WHEN: prior validated insights can improve answer quality. "
                        "DO NOT USE: if fresh metadata/SQL answer is sufficient."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                            "connector_name": {"type": "string"},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            })

        if "search_confluence_pages" in tool_name_set:
            tools.append({
                "type": "function",
                "function": {
                    "name": "search_confluence_pages",
                    "description": (
                        "Search Confluence pages and return concise excerpts. "
                        "USE WHEN: user asks about internal docs/processes/definitions. "
                        "DO NOT USE: if question is fully answerable from metadata/SQL."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "connector_name": {"type": "string"},
                            "space_key": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            })

        if "search_jira_issues" in tool_name_set:
            tools.append({
                "type": "function",
                "function": {
                    "name": "search_jira_issues",
                    "description": (
                        "Search Jira Server/Data Center issues via JQL-backed lookup. "
                        "USE WHEN: user asks to find tickets, complaints, incidents, or to inspect who owns similar Jira work. "
                        "DO NOT USE: when an exact issue key is already known and full body is needed."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "connector_name": {"type": "string"},
                            "project_key": {"type": "string"},
                            "assignee": {"type": "string"},
                            "status": {"type": "string"},
                            "issue_type": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            })

        if "get_jira_issue" in tool_name_set:
            tools.append({
                "type": "function",
                "function": {
                    "name": "get_jira_issue",
                    "description": (
                        "Fetch one Jira issue with body and routing details. "
                        "USE WHEN: the issue key is known or search results need to be opened."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "issue_key": {"type": "string"},
                            "connector_name": {"type": "string"},
                        },
                        "required": ["issue_key"],
                        "additionalProperties": False,
                    },
                },
            })

        if "create_jira_issue" in tool_name_set:
            tools.append({
                "type": "function",
                "function": {
                    "name": "create_jira_issue",
                    "description": (
                        "Create a Jira issue using explicit user intent, metadata owners, and recent assignee history for routing. "
                        "USE WHEN: the conversation context shows the user wants a Jira issue created or registered. "
                        "DO NOT USE: for complaints or feature discussion alone when no ticket creation is implied."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "description": {"type": "string"},
                            "request_kind": {
                                "type": "string",
                                "enum": ["bug_report", "feature_request", "task"],
                            },
                            "connector_name": {"type": "string"},
                            "context_fqn": {"type": "string"},
                            "project_key": {"type": "string"},
                            "assignee_hint": {"type": "string"},
                        },
                        "required": ["summary", "description", "request_kind"],
                        "additionalProperties": False,
                    },
                },
            })

        return tools

    @staticmethod
    def build_user_message(
        question: str,
        db_connectors: list[Any],
        metadata_connector_names: list[str],
        rag_enabled: bool = False,
        confluence_connector_names: list[str] | None = None,
        confluence_enabled: bool = False,
        jira_connector_names: list[str] | None = None,
        jira_enabled: bool = False,
        jira_create_enabled: bool = False,
        profile_tools_allowed: bool = False,
        recent_tables: list[str] | None = None,
        grounded_tables: list[Any] | None = None,
        prompt_mode: str | None = None,
    ) -> str:
        """
        Build user message with context about available connectors.

        Args:
            question: User's question.
            db_connectors: List of database connector items.
            metadata_connector_names: List of metadata connector names.

        Returns:
            Formatted user message string.
        """
        resolved_prompt_mode = prompt_mode or "tool_orchestration"
        connector_lines = "\n".join(
            (
                f"- name={item.name}; engine={item.connection.engine}; "
                f"map={PromptBuilder._mapping_signature(item)}; "
                f"sql_scope={PromptBuilder._sql_scope_signature(item)}"
            )
            for item in db_connectors
        )
        metadata_connector_lines = "\n".join(f"- {name}" for name in metadata_connector_names)
        confluence_lines = "\n".join(f"- {name}" for name in (confluence_connector_names or []))
        jira_lines = "\n".join(f"- {name}" for name in (jira_connector_names or []))
        recent_table_lines = "\n".join(
            PromptBuilder._format_table_hint_line(
                table_fqn=table,
                db_connectors=db_connectors,
            )
            for table in (recent_tables or [])[:5]
        )
        grounded_table_lines = PromptBuilder._grounded_table_lines(
            grounded_tables,
            db_connectors=db_connectors,
        )

        if resolved_prompt_mode == "direct_response":
            return f"Question: {question}\n"
        if resolved_prompt_mode == "discovery":
            database_context = (
                f"Database connectors:\n{connector_lines or '- none'}\n"
                if profile_tools_allowed
                else ""
            )
            return (
                f"Question: {question}\n"
                f"{database_context}"
                f"Metadata connectors:\n{metadata_connector_lines or '- none'}\n"
                f"RAG enabled: {str(rag_enabled).lower()}\n"
                f"Confluence connectors:\n{confluence_lines or '- none'}\n"
                f"Confluence enabled: {str(confluence_enabled).lower()}\n"
                f"Jira connectors:\n{jira_lines or '- none'}\n"
                f"Jira enabled: {str(jira_enabled).lower()}\n"
                f"Jira create enabled: {str(jira_create_enabled).lower()}\n"
                f"Recent grounded tables:\n{recent_table_lines or '- none'}\n"
            )
        return (
            f"Question: {question}\n"
            f"Database connectors:\n{connector_lines or '- none'}\n"
            f"Metadata connectors:\n{metadata_connector_lines or '- none'}\n"
            f"RAG enabled: {str(rag_enabled).lower()}\n"
            f"Confluence connectors:\n{confluence_lines or '- none'}\n"
            f"Confluence enabled: {str(confluence_enabled).lower()}\n"
            f"Jira connectors:\n{jira_lines or '- none'}\n"
            f"Jira enabled: {str(jira_enabled).lower()}\n"
            f"Jira create enabled: {str(jira_create_enabled).lower()}\n"
            f"Recent grounded tables:\n{recent_table_lines or '- none'}\n"
            f"Grounded schema hints:\n{grounded_table_lines or '- none'}\n"
        )

    @staticmethod
    def _mapping_signature(connector: Any) -> str:
        """Build mapping signature for a connector."""
        settings = connector.connection.extra_settings
        service = settings.get("metadata_service", "*")
        database = settings.get("metadata_database", "*")
        schema = settings.get("metadata_schema", "*")
        return f"{service}.{database}.{schema}"

    @staticmethod
    def _sql_scope_signature(connector: Any) -> str:
        """Build executable SQL scope hint for a connector."""
        engine = str(getattr(connector.connection, "engine", "") or "").strip().lower()
        if engine == "trino":
            return "any_catalog.any_schema.table"
        return "schema.table"

    @staticmethod
    def _grounded_table_lines(
        grounded_tables: list[Any] | None,
        db_connectors: list[Any] | None = None,
    ) -> str:
        lines: list[str] = []
        for table in grounded_tables or []:
            fqn = getattr(table, "fqn", None)
            columns = getattr(table, "columns", None)
            if not isinstance(fqn, str) or not fqn.strip():
                continue
            sample_columns = []
            if isinstance(columns, list):
                sample_columns = [
                    str(column).strip()
                    for column in columns
                    if isinstance(column, str) and column.strip()
                ][:8]
            columns_suffix = "; columns=" + ", ".join(sample_columns) if sample_columns else ""
            matched_on = getattr(table, "matched_on", None)
            matched_on_values = (
                [
                    str(item).strip()
                    for item in matched_on
                    if isinstance(item, str) and item.strip()
                ][:6]
                if isinstance(matched_on, list)
                else []
            )
            owners = getattr(table, "owners", None)
            owner_values = (
                [str(item).strip() for item in owners if isinstance(item, str) and item.strip()][:3]
                if isinstance(owners, list)
                else []
            )
            domains = getattr(table, "domains", None)
            domain_values = (
                [str(item).strip() for item in domains if isinstance(item, str) and item.strip()][
                    :3
                ]
                if isinstance(domains, list)
                else []
            )
            suspicious = MetadataSemanticGroundingService.is_suspicious_table(table)
            extras = [
                columns_suffix.lstrip("; "),
                (
                    "sql_ref="
                    + PromptBuilder._execution_reference_for_table(
                        table_fqn=fqn,
                        db_connectors=db_connectors or [],
                    )
                    if PromptBuilder._execution_reference_for_table(
                        table_fqn=fqn,
                        db_connectors=db_connectors or [],
                    )
                    else ""
                ),
                f"certification={getattr(table, 'certification', None) or 'none'}",
                f"tier={getattr(table, 'tier', None) or 'none'}",
                f"owners={', '.join(owner_values) if owner_values else 'none'}",
                f"domains={', '.join(domain_values) if domain_values else 'none'}",
                f"matched_on={', '.join(matched_on_values) if matched_on_values else 'none'}",
                f"suspicious={'yes' if suspicious else 'no'}",
            ]
            rendered_extras = "; ".join(item for item in extras if item)
            lines.append(f"- fqn={fqn.strip()}; {rendered_extras}")
        return "\n".join(lines)

    @staticmethod
    def _format_table_hint_line(
        *,
        table_fqn: str,
        db_connectors: list[Any],
    ) -> str:
        sql_ref = PromptBuilder._execution_reference_for_table(
            table_fqn=table_fqn,
            db_connectors=db_connectors,
        )
        if sql_ref is None:
            return f"- {table_fqn}"
        return f"- {table_fqn} (sql: {sql_ref})"

    @staticmethod
    def _execution_reference_for_table(
        *,
        table_fqn: str,
        db_connectors: list[Any],
    ) -> str | None:
        if not isinstance(table_fqn, str) or not table_fqn.strip():
            return None
        parts = [part.strip() for part in table_fqn.split(".") if part.strip()]
        if len(parts) < 4:
            return None
        service, database, schema, table = parts[0], parts[1], parts[2], parts[-1]
        connector = PromptBuilder._connector_for_mapping(
            db_connectors=db_connectors,
            service=service,
            database=database,
            schema=schema,
        )
        if connector is None:
            return None
        engine = str(getattr(getattr(connector, "connection", None), "engine", "") or "").lower()
        if engine == "clickhouse":
            return f"{schema}.{table}"
        if engine == "trino":
            return f"{database}.{schema}.{table}"
        if engine in {"greenplum", "impala"}:
            return f"{schema}.{table}"
        return None

    @staticmethod
    def _connector_for_mapping(
        *,
        db_connectors: list[Any],
        service: str,
        database: str,
        schema: str,
    ) -> Any | None:
        for connector in db_connectors:
            settings = getattr(getattr(connector, "connection", None), "extra_settings", None)
            if not isinstance(settings, dict):
                continue
            metadata_service = str(settings.get("metadata_service") or "").strip().lower()
            metadata_database = str(settings.get("metadata_database") or "").strip().lower()
            metadata_schema = str(settings.get("metadata_schema") or "").strip().lower()
            if metadata_service != service.strip().lower():
                continue
            if metadata_database != database.strip().lower():
                continue
            if metadata_schema and metadata_schema != schema.strip().lower():
                continue
            return connector
        return None

    @staticmethod
    def _default_prompt_mode(
        *,
        run_sql_allowed: bool,
        metadata_configured: bool,
        rag_configured: bool,
        confluence_configured: bool,
        jira_read_configured: bool,
        jira_write_configured: bool,
        profile_tools_allowed: bool,
    ) -> str:
        if run_sql_allowed:
            return "sql_generation"
        if (
            metadata_configured
            or rag_configured
            or confluence_configured
            or jira_read_configured
            or jira_write_configured
            or profile_tools_allowed
        ):
            return "discovery"
        return "direct_response"
