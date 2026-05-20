# Deployment

## 1. Режимы развёртывания

- Local/dev: Docker Compose (`compose.yaml`).
- Self-hosted prod: Kubernetes + Helm (`deploy/helm/rhetorica`).

## 2. Local topology

Инфраструктура:

- PostgreSQL
- Redis
- MinIO + bucket init job

Приложения:

- API (`apps/api`)
- Worker (`apps/worker`)
- Web (`apps/web`)

## 3. Dev bootstrap

1. `make bootstrap`
2. `make infra-up`
3. `make apps-up` или `make dev-up`
4. `make ps` / `make logs`

Rebuild without control-plane data loss:

1. `make dev-down`
2. `make dev-up`

`make dev-down` uses `docker compose down --remove-orphans` without `-v`, so named volumes (`postgres-data`) are preserved.

## 4. Production baseline (target)

- Выделенные namespace и secrets management.
- Read-only DWH credentials.
- Политики timeout/row/concurrency на уровне приложения и движка.
- Centralized logs + metrics + traces.
- Network policies на доступ к DWH и LLM endpoint.

## 5. Что важно не ломать

- API и Worker должны использовать один backend core (`packages/backend`).
- SQL guard нельзя обходить прямым execution path.
- Конфигурация подключений хранится в control-plane БД, секреты редактируются контролируемо.
- Chat execution path должен идти через guard + execution service, без прямого run from LLM.
