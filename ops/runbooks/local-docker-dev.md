# Local Docker Development

1. Copy `.env.example` to `.env` with `make bootstrap`.
2. Start infra with `make infra-up`.
3. Start app containers with `make apps-up` when runtime entrypoints exist.
4. Validate topology with `make compose-config`.

## Data persistence

- `compose.yaml` uses named volume `postgres-data` for PostgreSQL.
- Safe rebuild flow (data preserved):
  1. `make dev-down`
  2. `make dev-up`
- Do not pass `-v` to `docker compose down`, иначе volume будет удален.

## Related runbooks

- RAG Insight Pipeline local check: `ops/runbooks/rag-insight-pipeline-local.md`
