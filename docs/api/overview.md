# API Overview

## Базовые принципы

- API сейчас ориентирован на foundation POC (SQL guard + execution + connectors).
- Контракты описаны Pydantic-моделями в `packages/backend/src/rhetorica_backend/contracts`.
- Все SQL-операции идут через read-only policy.
- Ручки в этой документации не дублируем: source of truth — Swagger/OpenAPI.

## Swagger / OpenAPI

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI schema: `/openapi.json`

Локально обычно: `http://localhost:8000/docs`.

## Функциональные группы API

- health/runtime
- sql guard
- sql execution lifecycle
- connectors admin
- llm connectors admin
- openmetadata connectors admin
- rag insights review (`pending`, `approve`, `reject`) + reputation поля (`likes_count`)
- chat tool `search_rag_insights` (опциональный retrieval из Qdrant в tool-loop)
- chat streaming endpoint (`/v1/chat/ask/stream`) + conversation history endpoints under `/v1/chat/*`
