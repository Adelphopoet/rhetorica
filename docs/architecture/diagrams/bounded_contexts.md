# Bounded Contexts

```mermaid
flowchart TB
    subgraph RuntimeApps["Runtime Applications (thin)"]
        api[API]
        worker[Worker]
        web[Web]
        mcp[MCP Bridge]
    end

    subgraph Ownership["Identity & Ownership"]
        identity[User Accounts]
        chat_state[Chat Conversations]
    end

    subgraph Configuration["Connector & Workspace Configuration"]
        connectors[DB / LLM / Metadata / RAG / Confluence Connectors]
        workspace_skills[Workspace Skills]
    end

    subgraph Orchestration["Analyst Runtime"]
        chat[Chat Orchestration]
        streaming[Streaming Orchestration]
        scenarios[Scenario Catalog]
        exports[Analysis Export]
    end

    subgraph Knowledge["Semantic & Retrieval"]
        metadata[Metadata Access]
        rag[RAG Retrieval + Review]
        optimization[SQL Optimization]
    end

    subgraph Execution["Execution Runtime"]
        sql_guard[SQL Guard]
        execution[Execution]
        results[Result + Visualization]
    end

    subgraph Reserved["Reserved / Backlog Boundaries"]
        workspaces[Workspaces]
        dbt[dbt Registry]
        artifacts[Saved Artifacts / Sharing]
    end

    web --> api
    api --> identity
    api --> connectors
    api --> chat
    api --> scenarios
    api --> exports
    worker --> execution
    worker --> rag
    mcp --> chat

    identity --> chat_state
    connectors --> metadata
    workspace_skills --> optimization
    chat_state --> chat
    chat --> metadata
    chat --> optimization
    chat --> sql_guard
    streaming --> chat
    scenarios --> chat
    sql_guard --> execution
    execution --> results
    results --> exports
    rag --> chat
```

## Context Descriptions

| Context | Responsibility |
|---------|----------------|
| **Identity & Ownership** | Bootstrap user resolution today, resource ownership on chats and scenarios |
| **Connector Configuration** | All warehouse, LLM, metadata, RAG, and Confluence connector definitions |
| **Workspace Skills** | External markdown skill ingestion used by SQL optimization/orchestration |
| **Chat Orchestration** | Tool loop, capability surface, persistence of answer/evidence/SQL |
| **Streaming Orchestration** | SSE/WebSocket event production over the same governed chat core |
| **Scenario Catalog** | Reusable analyst flows with typed inputs and connector bindings |
| **Metadata Access** | OpenMetadata hydration and connector mapping |
| **RAG Retrieval + Review** | Insight retrieval plus pending/verified review workflow in Qdrant |
| **SQL Optimization** | Engine-aware prompt shaping and workspace-skill injection |
| **SQL Guard** | Read-only enforcement, limit normalization, violation reporting |
| **Execution** | Sync and queued execution against wired engines |
| **Result + Visualization** | Execution snapshot shaping and chart planning |
| **Analysis Export** | Deterministic HTML/PDF/notebook rendering from persisted snapshots |
| **Reserved Boundaries** | Separate workspaces/dbt/artifact domains are still backlog or placeholders |

## Architecture Rule

> Runtime applications (`apps/api`, `apps/worker`) stay thin. Product behavior belongs in `packages/backend`.
