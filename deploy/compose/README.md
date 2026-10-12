# Docker Compose

Local development runs from the repository root via `compose.yaml`.
This directory is reserved for future overrides like observability, CI smoke topology, or demo stacks.

The root Compose file includes the optimized direct agent harness for API, worker
and scenario-worker through a shared environment block. No local harness override
file is needed. `.env.example` lists all project switches, context limits and raw
artifact retention settings; explicit `.env` values override the defaults.
Dispatch remains disabled. Recreate the affected services with `docker compose
--profile apps up -d --no-deps api worker scenario-worker` after changing settings.
Provider-specific capabilities and compactor reasoning remain in the frontend LLM
connection settings. See the [harness configuration guide](../../docs/rhetorica/dlya-razrabotchika/prompt-caching-guide/index.md#настройки-context-harness)
for the Compose/Helm mapping and rollback procedure.
