# Repository Layout

## Структура монорепы

- `apps/api` — FastAPI transport/runtime слой.
- `apps/worker` — Celery runtime для фоновых задач.
- `apps/web` — Next.js приложение аналитика.
- `packages/backend` — backend core: domain + infra + contracts.
- `packages/ui` — UI primitives.
- `packages/contracts` — контракты API/events/schemas.
- `deploy` — deployment assets (compose/helm).
- `ops` — runbooks и эксплуатационные материалы.
- `tests` — e2e/performance/fixtures.

## Правило слоёв

1. Бизнес-логика — в `packages/backend`.
2. `apps/*` — thin runtime без дублирования доменной логики.
3. Интеграции с внешними системами — в `packages/backend/src/rhetorica_backend/infra/*`.
4. Контракты API/домена — через `packages/backend/src/rhetorica_backend/contracts/*`.

## Текущий статус реализации

- Выполнены каркасы `api`, `worker`, `web`.
- Реально реализованы SQL guard, execution flow, connector management.
- Остальные bounded contexts зарезервированы директорией и будут заполняться по roadmap.

