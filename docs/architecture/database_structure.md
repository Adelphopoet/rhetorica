# Database Structure

## Назначение

Этот файл описывает фактическую control-plane схему PostgreSQL и смежные внешние хранилища, которые использует текущая реализация.

Главное:

- `PostgreSQL` — source of truth для control-plane состояния.
- `Qdrant` — не часть этой схемы; там живут pending/verified RAG insights.
- Отдельной таблицы `workspace` пока нет: изоляция в основном идёт через `workspace_id` на ресурсах.
- Отдельных сущностей `saved_artifact` и `export_job` пока тоже нет: export строится из chat + execution snapshot.

## 1. Identity и ownership

### Таблица: `user_account`

Workspace-local субъект, на которого завязано владение чатами и сценариями.

Ключевые поля:

- `id` (PK)
- `workspace_id`
- `auth_provider`
- `auth_subject`
- `email`
- `display_name`
- `is_bootstrap_user`
- `created_at`, `updated_at`, `last_seen_at`

Ограничения:

- `UNIQUE(workspace_id, auth_provider, auth_subject)`

Примечания:

- Сейчас это основа bootstrap identity до полноценной внешней аутентификации.

## 2. Chat state

### Таблица: `chat_conversation`

Контейнер многотурового analyst chat.

Ключевые поля:

- `id` (PK)
- `workspace_id`
- `owner_user_id` -> `user_account.id`
- `title`
- `created_at`, `updated_at`

### Таблица: `chat_message`

Снимок отдельного user/assistant сообщения.

Ключевые поля:

- `id` (PK)
- `conversation_id` -> `chat_conversation.id`
- `role` (`user` | `assistant`)
- `content`
- `generated_sql`
- `execution_id`
- `context_tables` (JSON array)
- `metadata_sources` (JSON array)
- `claims` (JSON array)
- `evidence` (JSON array)
- `claim_evidence_links` (JSON array)
- `uncertainty_flags` (JSON array)
- `capability_trace` (JSON object)
- `optimization_trace` (JSON object)
- `selected_connector`
- `visualization_spec` (JSON)
- `warnings` (JSON array)
- `feedback`
- `feedback_updated_at`
- `created_at`

Ограничения:

- `UNIQUE(conversation_id, created_at, id)` для стабильного порядка сообщений

Примечания:

- Это уже не просто chat history. Тут лежит explainability snapshot ответа.
- Feedback по assistant message запускает RAG pipeline, но сам результат pipeline в Postgres не хранится.

## 3. SQL execution

### Таблица: `query_execution_history`

Жизненный цикл guarded SQL execution.

Ключевые поля:

- `execution_id` (PK)
- `workspace_id`
- `engine`
- `status` (`queued`, `running`, `succeeded`, `failed`, `blocked`, `cancel_requested`, `cancelled`)
- `submitted_sql`
- `normalized_sql`
- `timeout_ms`
- `row_limit`
- `row_count`
- `duration_ms`
- `query_id`
- `error_message`
- `warnings` (JSON)
- `violations` (JSON)
- `preview_payload` (JSON)
- `connection_summary` (JSON, redacted)
- `created_at`, `updated_at`, `started_at`, `finished_at`, `cancel_requested_at`

Примечания:

- `queued` status используется async path через Celery.
- `preview_payload` хранит result preview, чтобы execution detail и export не зависели только от live warehouse ответа.
- Выполнение всегда проходит через guard; direct warehouse writes тут не предполагаются.

## 4. Connector configuration

### Таблица: `connector_config`

Runtime подключения к DWH.

Ключевые поля:

- `id` (PK)
- `workspace_id`
- `name`
- `engine`
- `host`, `port`
- `username`
- `password_secret_ref`
- `password` (legacy fallback)
- `database`, `catalog`, `schema_name`
- `secure`, `verify`
- `extra_settings` (JSON)
- `created_at`, `updated_at`

Ограничения:

- `UNIQUE(workspace_id, name)`

Используемые runtime mapping-поля в `extra_settings`:

- `metadata_service`
- `metadata_database`
- `metadata_schema`

Они помогают chat/runtime выбрать правильный warehouse connector под найденный metadata context.

### Таблица: `llm_connector_config`

OpenAI-compatible LLM подключения.

Ключевые поля:

- `id` (PK)
- `workspace_id`
- `name`
- `base_url`
- `model`
- `api_key_secret_ref`
- `api_key` (legacy fallback)
- `endpoint_path`
- `temperature`, `max_tokens`
- `headers` (JSON)
- `extra_settings` (JSON)
- `created_at`, `updated_at`

Ограничения:

- `UNIQUE(workspace_id, name)`

### Таблица: `openmetadata_connector_config`

Источник metadata context для chat/runtime.

Ключевые поля:

- `id` (PK)
- `workspace_id`
- `name`
- `provider` (`openmetadata`)
- `base_url`
- `api_token_secret_ref`
- `api_token` (legacy fallback)
- `verify_tls`
- `mcp_tables_url`
- `service_name`, `database_name`, `schema_name`
- `table_limit`
- `fields`
- `extra_settings` (JSON)
- `created_at`, `updated_at`

Ограничения:

- `UNIQUE(workspace_id, name)`

### Таблица: `rag_connector_config`

Конфигурация vector store для reusable insights.

Ключевые поля:

- `id` (PK)
- `workspace_id`
- `name`
- `provider` (`qdrant`)
- `base_url`
- `api_key_secret_ref`
- `api_key` (legacy fallback)
- `collection_name`
- `vector_size`
- `distance`
- `prefer_grpc`
- `embedding_model`
- `confidence_threshold`
- `extra_settings` (JSON)
- `created_at`, `updated_at`

Ограничения:

- `UNIQUE(workspace_id, name)`

### Таблица: `confluence_connector_config`

Подключение к Confluence как дополнительному knowledge source.

Ключевые поля:

- `id` (PK)
- `workspace_id`
- `name`
- `provider` (`confluence`)
- `base_url`
- `username`
- `api_token_secret_ref`
- `api_token` (legacy fallback)
- `auth_type`
- `verify_tls`
- `space_key`
- `page_limit`
- `extra_settings` (JSON)
- `created_at`, `updated_at`

Ограничения:

- `UNIQUE(workspace_id, name)`

Общее примечание по connector tables:

- `*_secret_ref` — основной путь хранения секретов.
- plaintext-поля (`password`, `api_key`, `api_token`) остались как legacy fallback и не должны быть нормой для новых записей.

## 5. Workspace knowledge

### Таблица: `workspace_skill`

Снапшот и метаданные внешнего markdown-skill, который можно подключать в runtime.

Ключевые поля:

- `id` (PK)
- `workspace_id`
- `name`
- `capability_family`
- `engine_scope`
- `priority`
- `source_kind`
- `source_url`
- `status`
- `validation_status`
- `title`
- `summary`
- `content_markdown`
- `content_bundle` (JSON bundle of resolved markdown assets such as `README.md` and `rules/*.md`)
- `content_sha256`
- `last_validated_at`
- `created_at`, `updated_at`

Ограничения:

- `UNIQUE(workspace_id, name)`

Примечания:

- Контент skill хранится прямо в Postgres как validated snapshot.
- Для `SKILL.md` runtime может хранить не только корневой markdown, но и связанный markdown bundle со вспомогательными файлами.
- Это уже часть runtime knowledge, а не просто admin metadata.

## 6. Scenario catalog

### Таблица: `scenario_definition`

Переиспользуемый analyst workflow с typed input schema и connector bindings.

Ключевые поля:

- `id` (PK)
- `slug`
- `workspace_id`
- `owner_user_id` -> `user_account.id` (nullable для official scope)
- `scope` (`official` | `personal`)
- `title`
- `summary`
- `description`
- `input_schema` (JSON)
- `prompt_template`
- `tool_policy` (JSON)
- `default_connector_bindings` (JSON)
- `status`
- `version`
- `created_at`, `updated_at`, `published_at`

Ограничения:

- `UNIQUE(workspace_id, slug)`

### Таблица: `scenario_run`

Факт запуска конкретного scenario definition.

Ключевые поля:

- `id` (PK)
- `scenario_definition_id` -> `scenario_definition.id`
- `workspace_id`
- `owner_user_id` -> `user_account.id`
- `resolved_version`
- `input_payload` (JSON)
- `conversation_id` -> `chat_conversation.id`
- `created_at`

Примечания:

- Run не хранит отдельный persisted artifact; он ссылается на conversation/chat snapshot.

## 7. Внешние хранилища вне PostgreSQL

### Qdrant

Хранит reusable insights для RAG:

- pending records после feedback pipeline
- verified/rejected status
- compact summary, explanation, tags, provenance, reputation payload

Это не control-plane source of truth для chat/execution history.

### Redis / Celery backend

Используется для:

- брокера async SQL execution
- брокера async RAG feedback pipeline
- task result transport для queued execution path

## 8. Что пока отсутствует как first-class schema

- отдельная таблица `workspace`
- отдельная таблица `saved_analysis_artifact`
- отдельная таблица `export_job`
- отдельное persisted dbt registry storage

Это не ошибка документации. Это реальное текущее состояние системы.
