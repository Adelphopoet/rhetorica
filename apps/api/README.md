# API Runtime

Thin FastAPI runtime layer.

## Responsibilities

- HTTP transport
- auth/session boundaries
- request/response mapping
- runtime composition and startup wiring
- streaming and API-specific concerns

Shared business logic belongs in `packages/backend`.
