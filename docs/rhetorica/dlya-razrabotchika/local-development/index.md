---
title: Локальный запуск
linkTitle: Локальная разработка
weight: 10
---

# Локальный запуск

Локальный стенд поднимается Docker Compose из корня репозитория. Одна команда `make dev-up` собирает и запускает PostgreSQL, Redis, Qdrant, MinIO, Temporal, API, worker, scenario-worker, execution runtime, coding query gateway и веб-интерфейс.

Контейнеры API и worker монтируют исходники репозитория. Первый старт ставит Python- и frontend-зависимости внутри контейнеров, поэтому он заметно дольше повторного.

## Что нужно на машине

- Docker с Compose v2 (`docker compose`);
- `make` и Git;
- сеть для загрузки базовых образов и пакетов.

Python 3.12 из `.python-version` и `uv` нужны, если миграции или тесты запускаются на хосте. Node.js 22 и pnpm нужны только для frontend-проверок на хосте: контейнер `web` ставит зависимости сам.

## Первый запуск

Из корня репозитория:

```bash
make bootstrap
make dev-up
```

`make bootstrap` копирует `.env.example` в `.env`, если файла ещё нет, и не перезаписывает уже существующий `.env`. Секреты остаются в `.env`. Не добавляйте их в Git, fixtures и документацию.

`make dev-up` собирает образы профиля `apps` и поднимает весь стек. Контейнер API перед стартом сам применяет миграции PostgreSQL. Дождитесь, пока `api` и `web` станут доступны: первый `uv sync` и `pnpm install` занимают несколько минут.

Проверка:

```bash
make ps
curl -fsS http://localhost:8000/healthz
```

Откройте [http://localhost:3000](http://localhost:3000). Локальный администратор из `.env.example`: логин `admin`, пароль `admin`. OpenAPI доступен на [http://localhost:8000/docs](http://localhost:8000/docs).

Пустой стенд отвечает на вход, но не отвечает в чате, пока в административной консоли не создан LLM-коннектор. Подключения к DWH, GitLab и MCP тоже задаются там, а не в `.env`.

## Адреса

| Сервис | Адрес | Учётные данные из `.env.example` |
| --- | --- | --- |
| Веб | http://localhost:3000 | `admin` / `admin` |
| API | http://localhost:8000 | health: `/healthz` |
| PostgreSQL | `localhost:5432` | `rhetorica` / `rhetorica`, база `rhetorica` |
| Redis | `localhost:6379` | без пароля |
| Qdrant | http://localhost:6333 | без ключа |
| MinIO API | http://localhost:9000 | `minio` / `minio123`, бакет `rhetorica-dev` |
| MinIO Console | http://localhost:9001 | `minio` / `minio123` |
| Temporal | `127.0.0.1:7233` | namespace `default` |
| Coding query gateway | `127.0.0.1:8092` | health: `/healthz` |

Порты задаются в `.env`: `WEB_PORT`, `API_PORT`, `POSTGRES_PORT` и остальные `*_PORT`. Если меняете порт веба или API, обновите `RHETORICA_PUBLIC_URL` и `RHETORICA_PUBLIC_API_URL` теми же адресами.

Приём новых сценариев выключен: `SCENARIO_WORKFLOWS_ENABLED=false`. Temporal и scenario-worker при этом запускаются вместе со стеком.

## Повседневные команды

```bash
make logs
make ps
make dev-down
```

`make logs` показывает хвост логов всех сервисов. `make dev-down` останавливает стек и удаляет контейнеры, тома с данными сохраняются.

После правки `.env` пересоздайте затронутые сервисы. `restart` не перечитывает окружение:

```bash
docker compose --profile apps up -d --no-deps api worker scenario-worker
```

Полная пересборка:

```bash
make dev-up
```

Миграции с хоста, если нужен схема до сборки приложений:

```bash
make infra-up
make db-migrate
```

`make db-migrate` ждёт PostgreSQL и вызывает Alembic через локальный `uv`. URL хоста собирается из `POSTGRES_*` в `.env` и указывает на `localhost`, а не на имя сервиса `postgres`.

Очистка данных PostgreSQL, Redis, Qdrant и MinIO:

```bash
make dev-down-clean
```

## Проверки на хосте

```bash
uv run --all-packages --all-extras pytest -n auto --dist loadfile
pnpm lint
pnpm typecheck
pnpm test
pnpm build
helm lint deploy/helm/rhetorica
```

## Coding в локальном Docker

После миграций поднимите `execution-runtime`, `coding-query-gateway` и `coding-worker` вместе с API, PostgreSQL, MinIO и Temporal. Выберите `CODING_RUNTIME_MODE=local_docker` и environment profile с digest реально собранного образа. После изменения runner пересоберите его образ; worker читает backend-код из workspace.

Тесты runner запускаются в образе `rhetorica-execution-runtime`, где установлены Git, ripgrep и dbt. API-образ этих исполняемых файлов не содержит. Платные LLM smoke выполняйте последовательно.

`agent_session` и `agent_session_item` — общий SDK Session store для разговоров и coding tasks. Provider usage каждого coding model call хранится в `coding_iteration_reservation.usage_json`; `estimated_request_tokens` — оценка контекста, не платёжный счётчик. `chat_conversation.llm_*` содержит суммарный фактический расход разговора, включая обсуждение, специалистов и компактирование.

Для ручного компактирования `CODING_CONTEXT_MAX_PROMPT_TOKENS` задаёт порог стоимости входа (по умолчанию 32000 оценочных токенов). `CODING_CONTEXT_RETAINED_TOOL_TOKENS` ограничивает полные недавние выводы (8000). Максимальное представление одного tool output — 4000 оценочных токенов. Нативное компактирование сохраняет порог коннектора; сокращение tool outputs работает и в этом режиме.

Миграция общего Session store сохраняет историю чатов. Downgrade с существующей историей coding tasks отклоняется: сначала требуется отдельный план сохранения этих данных.
