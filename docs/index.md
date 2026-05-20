# Documentation

Rhetorica — self-hosted AI workspace for governed analytics in enterprise DWH.

## Quick Start

1. [Architecture](architecture/architecture.md) — technical overview
2. [Local Development](development/local_development.md) — how to run locally

## Tech Stack

| Component | Technology |
|-----------|------------|
| **Backend** | Python 3.12, FastAPI, SQLAlchemy |
| **Frontend** | Next.js 14+, TypeScript, React |
| **Database** | PostgreSQL 16 |
| **Cache** | Redis 7 |
| **Vector Store** | Qdrant |
| **Storage** | MinIO (S3-compatible) |
| **Query Engines** | ClickHouse, Trino |
| **Package Managers** | uv (Python), pnpm 10.x (Node) |

## Documentation Map

- **Architecture**
  - [Architecture Overview](architecture/architecture.md)
  - [System Diagrams](architecture/diagrams/)
    - [Layers](architecture/diagrams/layers.md)
    - [Bounded Contexts](architecture/diagrams/bounded_contexts.md)
    - [Query Lifecycle](architecture/diagrams/query_lifecycle.md)
  - [Database Structure](architecture/database_structure.md)
- **Development**
  - [Local Development](development/local_development.md)
  - [Repository Layout](development/repository_layout.md)
- **Backend Code Docs**
  - [Backend Code Docs Index](backend/index.md)
- **API**
  - [Overview](api/overview.md)
- **Operations**
  - [Deployment](operations/deployment.md)
- **Security**
  - [Baseline](security/baseline.md)

## Current Status

Working early POC implemented:

- SQL guard (`/v1/sql/guard`) using SQLGlot
- SQL execution (`/v1/sql/execute`, history, cancel) for ClickHouse/Trino
- Chat SSE endpoint (`/v1/chat/ask/stream`) with conversation flow
- Connector registry for database, LLM, and OpenMetadata
- Basic analyst workspace in web

**Open areas:** semantic grounding quality, explainability depth, reuse/sharing, pilot-ready governance
