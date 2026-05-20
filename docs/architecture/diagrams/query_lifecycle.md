# Query Lifecycle

## Governed Chat Flow

```mermaid
flowchart LR
    question[Question] --> identity[Resolve current user]
    identity --> convo[Load or create conversation]
    convo --> capability[Build capability surface]
    capability --> tools[LLM tool loop]
    tools --> enrich[Metadata / RAG / Confluence / Skills]
    enrich --> sql[SQL draft + optimization]
    sql --> guard[SQL Guard]
    guard --> exec[Optional execution]
    exec --> result[Result + visualization plan]
    result --> persist[Persist assistant snapshot]
    persist --> answer[Answer to analyst]
```

## Streaming Transports

```mermaid
flowchart TB
    client[Client] --> sse["POST /v1/chat/ask/stream"]
    sse --> orchestrator[StreamingChatOrchestrator]
    orchestrator --> events[thinking / tool / execution / chunk / done]
    orchestrator --> final[Persist final assistant message]
```

## Queue-Backed SQL Execution

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant PG as PostgreSQL
    participant Redis as Celery Broker
    participant Worker
    participant Guard as SqlGuardService
    participant Engine as DWH Engine

    Client->>API: POST /v1/sql/executions
    API->>PG: create queued history record
    API->>Redis: enqueue sql_execute.run
    Redis->>Worker: deliver task
    Worker->>Guard: validate / normalize SQL
    Guard-->>Worker: allowed or blocked
    alt allowed
        Worker->>Engine: execute prepared query
        Engine-->>Worker: rows / query_id / error
    end
    Worker->>PG: update execution history
    Client->>API: GET /v1/sql/executions/{id}
    API->>PG: read detail + merge task result if ready
```

## Scenario Run Flow

```mermaid
flowchart LR
    definition[Scenario definition] --> inputs[Validate typed inputs]
    inputs --> prompt[Render prompt template]
    prompt --> chat[Delegate to chat orchestrator]
    chat --> response[Chat response]
    response --> run[Persist scenario_run linked to conversation]
```

## RAG Feedback Pipeline

```mermaid
flowchart TB
    feedback[Assistant message marked useful] --> enqueue[API enqueues task]
    enqueue --> hard[Hard gate]
    hard --> deterministic[Deterministic rejection filters]
    deterministic --> llm[LLM gate]
    llm --> embed[Embedding + similarity merge]
    embed --> pending[Qdrant pending insight]
    pending --> review[Admin approve / reject]
    review --> verified[Verified insight retrievable in chat]
```

## Invariants

- **No execution without guard**: both sync and queued SQL paths go through `SqlGuardService`.
- **LLM never executes directly**: the model can draft SQL, but warehouses are reached only through `ExecutionService`.
- **Snapshots matter**: assistant messages persist SQL, evidence, warnings, connector choice, capability trace, optimization trace, and optional visualization.
- **Ownership is enforced before history reuse**: conversations and scenarios are resolved against the current workspace user.
- **Current auth is bootstrap-based**: full external auth is not the mainline runtime yet.
