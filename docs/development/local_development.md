# Local Development

## 1. Требования

- Docker + Docker Compose
- `make`
- `uv` (для Python workspace)
- `pnpm`/Node.js (для web workspace)
- Python `3.12` для host-side `uv` команд (`.python-version` уже добавлен в репозиторий)

## 2. Базовый цикл

1. Подготовить env:
   - `make bootstrap`
2. Поднять инфраструктуру:
   - `make infra-up`
3. Поднять приложения:
   - `make apps-up` или `make dev-up`
4. Проверить состояние:
   - `make ps`
   - `make logs`
5. Остановить контур:
   - `make apps-down` или `make dev-down`

## 3. Полезные команды

- `make compose-config` — валидация compose topology.
- `make uv-sync` — синхронизация Python workspace зависимостей.
- `make uv-lock` — обновить `uv.lock`.
- `make web-install` — обновить `pnpm-lock.yaml`.
- `make db-migrate` — host-side миграции через опубликованный Postgres порт с кредами из `.env`.

## 4. Docker-first policy

Локальная разработка ведётся через контейнеры. Не поддерживаем отдельный "host-only" путь, который расходится с `compose.yaml`.

## 5. Что поднимается в compose

Infra-сервисы:

- PostgreSQL
- Redis
- MinIO
- MinIO bucket init

App-сервисы (profile `apps`):

- API (`apps/api`)
- Worker (`apps/worker`)
- Web (`apps/web`)

## 6. Миграции базы данных

Миграции применяются автоматически при старте API.

Host-side команды миграций используют креды из `.env`, но подключаются к Postgres через `localhost:${POSTGRES_PORT}`. Это совпадает с локальным `docker compose` сценарием и не требует ручного переопределения `DATABASE_URL`.

Для ручного управления:

```bash
make db-migrate        # применить миграции
make db-migrate-make   # создать новую
make db-downgrade      # откатить
```

Первый `make dev-up` может занимать несколько минут: `api`/`worker` делают `uv sync`, а `web` выполняет `pnpm install` внутри контейнера перед стартом dev server.

Полная документация: [Database Migrations](database_migrations.md)
