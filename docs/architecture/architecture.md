# Architecture

> Status: code-verified against `apps/api`, `apps/worker`, `packages/backend`, and current Alembic schema.

## 1. Tech Stack

| Component | Technology |
|-----------|------------|
| **Backend runtime** | Python 3.12, FastAPI, Pydantic, SQLAlchemy |
| **Async jobs** | Celery + Redis |
| **SQL safety** | SQLGlot-based guard |
| **Frontend** | Next.js, React, TypeScript |
| **Control-plane DB** | PostgreSQL 16 |
| **Vector store** | Qdrant |
| **Embeddings** | FastEmbed |
| **Query engines wired today** | ClickHouse, Trino |
| **Metadata sources** | OpenMetadata, Confluence, runtime connector mapping |
| **LLM access** | OpenAI-compatible chat-completions endpoints |

Notes:

- `packages/backend` owns domain logic. `apps/api` and `apps/worker` are thin runtimes.
- `infra/engine_greenplum` exists, but current `ExecutionService` wires only ClickHouse and Trino.
- `domain/dbt_registry` and `domain/workspaces` are reserved boundaries for future work, not active product cores yet.

## 2. Runtime Surfaces

| Surface | Purpose |
|---------|---------|
| `POST /v1/chat/ask/stream` | SSE streaming chat |
| `/v1/auth/*` | Local account login/session for web and API runtime |
| `POST /v1/sql/execute` | Immediate guarded SQL execution |
| `POST /v1/sql/executions` | Queue-backed SQL execution via Celery |
| `/v1/scenarios` | Scenario catalog CRUD + scenario runs |
| `/v1/chat/conversation/{conversation_id}/message/{message_id}/export/{format}` | Deterministic export from persisted chat/execution snapshot |
| `/v1/admin/rag-insights/*` | Review queue for reusable RAG insights |
| `/v1/admin/skills` | Workspace skill ingestion and validation |
| `/mcp/*` | MCP bridge over JSON-RPC and agent websocket transports |

## 3. System Layers

| Layer | Description |
|-------|-------------|
| **Access Plane** | Web app, HTTP API, SSE, MCP transport |
| **Orchestration Plane** | Chat orchestration, streaming orchestration, scenarios, export rendering, RAG curation |
| **Semantic Plane** | Metadata retrieval, connector mapping, Confluence search, RAG retrieval, workspace skill prompts |
| **Execution Plane** | SQL guard, SQL routing, execution lifecycle, queue-backed worker execution |
| **State Plane** | PostgreSQL control-plane state and snapshots, Redis broker/backend, Qdrant insight store |

> See: [System Layers Diagram](diagrams/layers.md)

## 4. Bounded Contexts

Implemented and active:

- identity and ownership
- connector management
- chat conversation state
- chat orchestration and tool loop
- metadata access
- SQL optimization and workspace skills
- SQL guard
- execution
- result shaping and visualization planning
- RAG retrieval and RAG curation/review
- scenario catalog
- analysis export

Present but not yet first-class product centers:

- workspaces as a separate domain object; current isolation is mostly `workspace_id` on resources
- dbt registry as a dedicated runtime subsystem; semantics currently come from OpenMetadata plus runtime mappings
- saved artifacts / sharing as dedicated persisted objects; export still renders from chat + execution snapshots

**Architecture rule:** runtime applications stay thin. If domain behavior lands in `apps/api` or `apps/worker`, the boundary is being violated.

> See: [Bounded Contexts Diagram](diagrams/bounded_contexts.md)

## 5. Core Runtime Flows

### Governed chat

`Question -> conversation bootstrap -> capability surface bootstrap -> LLM tool loop -> metadata/RAG/Confluence/workspace-skill enrichment -> SQL draft/optimization -> SQL guard -> optional execution -> answer/evidence/visualization snapshot -> conversation persistence`

### Streaming chat

The same orchestration path is exposed through SSE and WebSocket transports. The streaming runtime emits thinking/tool/execution/chunk events while still persisting the final assistant snapshot into chat history.

### Async SQL execution

`POST /v1/sql/executions -> queued history record in Postgres -> Celery task -> worker -> ExecutionService.execute_prepared() -> guarded execution -> result/history update`

### Scenario run

`Scenario definition -> prompt rendering from typed inputs -> delegated chat orchestration -> scenario_run record + linked conversation`

### RAG feedback curation

`assistant feedback=useful -> API enqueue -> worker hard gate -> deterministic rejection filters -> LLM gate -> embedding -> Qdrant pending insight -> admin approve/reject -> retrievable verified insight`

> See: [Query Lifecycle Diagram](diagrams/query_lifecycle.md)

## 6. Invariants

- No SQL execution without `SqlGuardService`.
- LLMs never talk to warehouses directly; execution goes only through `ExecutionService`.
- The user-visible answer must remain transparent: SQL, warnings, evidence, connector choice, and execution metadata are persisted with the assistant message.
- Ownership is enforced per user/resource where implemented; current auth is local-account based with JWT-bound workspace context. Bootstrap auth is dev-only fallback, not the main runtime path.
- Postgres is the source of truth for control-plane state. Qdrant stores reusable insight records, not authoritative chat/execution state.

## 7. Storage Model

### PostgreSQL

Source of truth for:

- users and ownership
- chat conversations/messages
- connector configs
- execution history
- workspace skills
- scenario definitions and runs

### Redis / Celery backend

Used for:

- async SQL execution dispatch
- async RAG feedback dispatch
- task result transport for queued execution reads

### Qdrant

Used for:

- pending and verified reusable RAG insights
- semantic similarity merge/dedup in the feedback pipeline

## 8. Deployment Notes

| Environment | Target |
|-------------|--------|
| Local/Dev | Docker Compose |
| Self-hosted | Kubernetes + Helm |

Operational facts from current code:

- API startup runs Alembic migrations.
- Worker exposes Celery tasks: `sql_guard.check`, `sql_execute.run`, `sql_execute.cancel`, `rag_insights.process_feedback`.
- Export is synchronous today and renders from persisted chat/execution snapshots; artifact-first storage is still backlog.

## 9. What Is Documented Elsewhere

- Database schema: [Database Structure](database_structure.md)
- API contracts: [API Overview](../api/overview.md)
- Diagrams: [diagrams/](diagrams/)
