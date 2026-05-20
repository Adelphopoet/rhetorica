# System Layers Diagram

```mermaid
flowchart TB
    subgraph Access["Access Plane"]
        web[Next.js Web]
        http[FastAPI HTTP]
        sse[SSE Chat Stream]
        ws[WebSocket Chat]
        mcp[MCP Bridge]
    end

    subgraph Orchestration["Orchestration Plane"]
        chat[Chat Orchestrator]
        streaming[Streaming Orchestrator]
        scenarios[Scenario Catalog]
        exports[Analysis Export]
        rag_curation[RAG Feedback Curation]
    end

    subgraph Semantic["Semantic Plane"]
        metadata[OpenMetadata Retrieval]
        confluence[Confluence Search]
        rag_retrieval[RAG Insight Retrieval]
        skills[Workspace Skills]
        connector_map[Connector Mapping]
    end

    subgraph Execution["Execution Plane"]
        guard[SQL Guard]
        router[SQL Router]
        exec_service[Execution Service]
        adapters[Adapters<br/>ClickHouse, Trino]
        worker[Celery Worker]
    end

    subgraph State["State Plane"]
        postgres[PostgreSQL]
        redis[Redis / Celery Backend]
        qdrant[Qdrant]
    end

    Access --> Orchestration
    Orchestration --> Semantic
    Orchestration --> Execution
    Semantic --> State
    Execution --> State
    worker --> exec_service
    worker --> qdrant
    exec_service --> adapters
```

### Layer Descriptions

| Layer | Responsibility |
|-------|----------------|
| **Access** | Web UI plus HTTP/SSE/WebSocket/MCP entrypoints |
| **Orchestration** | Governed chat, streaming, scenarios, export rendering, feedback curation |
| **Semantic** | Metadata, RAG, Confluence, workspace-skill knowledge, connector mapping |
| **Execution** | Guarded SQL routing and warehouse execution, both sync and queued |
| **State** | Durable state in Postgres, async transport in Redis, reusable insights in Qdrant |
