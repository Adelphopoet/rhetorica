# Rhetorica

Self-hosted AI workspace for governed analytics.

## Documentation

- Published Russian documentation: [`docs/rhetorica`](docs/rhetorica/)
- Canonical FR, NFR and UC: [`openspec/specs`](openspec/specs/)

```bash
npx --yes @fission-ai/openspec@1.7.0 validate --all --strict --no-interactive
python tools/sync_openspec_docsy.py --check
```

## Monorepo layout

- `apps/api` - FastAPI transport layer and API runtime.
- `apps/worker` - background runtime for imports, heavy queries, exports.
- `apps/web` - Next.js analyst workspace.
- `packages/backend` - shared Python backend core: domain, infra, contracts, persistence.
- `packages/ui` - shared frontend primitives.
- `packages/contracts` - API, event, and schema contracts.
- `deploy` - Docker-first local bootstrap and Helm chart.
- `docs/rhetorica` - product documentation.
- `tests` - cross-app fixtures, e2e, performance scenarios.

## Local development

Local development is Docker-first. The launch steps live in [Локальный запуск](docs/rhetorica/dlya-razrabotchika/local-development/index.md).

```bash
make bootstrap
make dev-up
```

- Infrastructure and app containers come from `compose.yaml`: PostgreSQL, Redis, Qdrant, MinIO, Temporal, API, worker, web.
- Python packages are managed through a `uv` workspace.
- Frontend packages are managed through a `pnpm` workspace.

## Test workflow

### Fast path

- `make test` - run the default Python and web test suites.
- `make test-parallel` - run Python tests through `pytest-xdist` with `-n auto --dist loadfile`, then run web tests.

### Python

```bash
# Full Python suite with workspace extras installed
uv run --all-packages --all-extras pytest

# Parallel Python run
uv run --all-packages --all-extras pytest -n auto --dist loadfile

# Single file / single test / keyword
uv run pytest apps/api/tests/unit/test_chat_routes.py
uv run pytest apps/api/tests/unit/test_chat_routes.py::test_chat_remembers_context_across_turns -xvs
uv run pytest -k chat

# Coverage
uv run pytest --cov=packages/backend --cov=apps/api
```

`pytest` runs with `--import-mode=importlib`, so duplicate test filenames in different directories no longer break collection.

### Frontend

```bash
pnpm test
```

## Architectural direction

- Shared backend logic lives in `packages/backend`, not inside the API runtime.
- Backend stays a modular monolith, split by bounded context.
- SQL governance is a product capability and must stay explicit.
- dbt artifacts are normalized into an internal semantic registry.
- The golden path stays: question -> semantic plan -> SQL -> guarded execution -> result -> chart -> saved answer.
