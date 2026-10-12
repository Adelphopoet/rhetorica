---
title: Исполнение сценариев
weight: 65
description: Self-hosted Temporal, worker, права и управление включением сценариев.
---

## Включение

Для локального тестирования соберите и запустите приложения:

```bash
docker compose -f compose.yaml -f compose.scenarios.local.yaml --profile apps up -d --build
```

Overlay собирает production-образы API, web и workers, включает workflow-запуски и выполняет миграции API. Temporal использует основной PostgreSQL приложения: схемы `temporal_persistence` и `temporal_visibility` с отдельными ролями и `search_path`. Служебная схема `temporal_extensions` содержит `btree_gin` для поиска запусков. Сервис `temporal-postgres-init` подготавливает роли и расширение; далее Temporal применяет свои миграции. Обе роли не имеют доступа к таблицам приложения в `public`. Не удаляйте volumes при обновлении. Локальный overlay использует общее зашифрованное хранилище `.rhetorica/secrets.enc.json` для API и обоих workers; каталог `.rhetorica` сохраняется между пересборками. Если раньше ключи коннекторов задавались только через environment secret backend, перенесите их в это хранилище или повторно сохраните соответствующие ключи в настройках коннекторов. Monaco поставляется с frontend-образом и не требует публичного CDN.

При обновлении прежнего локального развёртывания с контейнером `temporal-postgres` сначала выполните:

```bash
.venv/bin/python tools/migrate_temporal_postgres.py
```

Скрипт останавливает Temporal и scenario-worker, сохраняет обе БД в `.rhetorica/backups/temporal-*`, переносит таблицы через временные БД без изменения содержимого истории и сверяет число записей. Импорт обоих хранилищ выполняется одной транзакцией. Непустые целевые схемы скрипт не перезаписывает. Старый контейнер останавливается, volume сохраняется. Затем выполните команду Compose выше. Не используйте `--remove-orphans` до проверки перенесённой истории. При сбое импорта сохраняются dumps и временные БД для восстановления; не запускайте Temporal на незавершённой миграции. До приёма новых запусков возможен возврат к старому хранилищу; после новых запусков нужен перенос актуальной истории обратно.

Для общей БД не используйте destructive-команды `temporal-sql-tool`: в версии 1.29.3 перечисление таблиц для сброса привязано к `public`. Миграции `setup-schema` / `update-schema` используют роли соответствующих stores. Общая БД делит CPU, память, подключения и резервное копирование с приложением; при росте нагрузки вынесите Temporal на отдельный PostgreSQL.

В Kubernetes разворачивайте Temporal отдельно от Rhetorica. Настройте persistence, visibility, резервное копирование и схему обновлений по [руководству Temporal](https://docs.temporal.io/self-hosted-guide/deployment). Пример значений Rhetorica:

```yaml
scenarioWorkflows:
  enabled: true
  temporal:
    address: temporal-frontend.temporal.svc.cluster.local:7233
    namespace: rhetorica
    taskQueue: rhetorica-scenarios-v1
    tlsSecret: temporal-client-tls
    serverName: temporal.internal
  worker:
    enabled: true
    replicaCount: 1
    concurrency: 2
```

Namespace должен существовать в Temporal. `tlsSecret` содержит `ca.crt`, `tls.crt`, `tls.key`; оставьте пустым при соединении без mTLS внутри доверенной сети. Worker наследует образ, `extraEnvFrom`, connector secrets и корпоративные CA существующего worker. Учитывайте дополнительный pod и его resource requests в квоте namespace. Новый worker не создаёт Service и не увеличивает квоту Services. Ограничьте concurrency с учётом лимитов DWH и sandbox.

## Откат и наблюдение

Отключите `scenarioWorkflows.enabled` / `SCENARIO_WORKFLOWS_ENABLED`, сохранив `scenarioWorkflows.worker.enabled=true`. Принятые запуски, решения по подтверждениям, просмотр истории и отмена продолжают работать. Удаление worker или Temporal не является штатным способом отката.

Проверяйте очередь Temporal `rhetorica-scenarios-v1`, недоставленные записи `scenario_outbox` и `last_error`, число попыток, состояния `scenario_workflow_run` и `scenario_step_run`, возраст ожидающих подтверждений и расходы токенов в диагностике шагов. SSE-восстановление использует последовательность событий, `Last-Event-ID` и параметр `after`.

Artifacts и SQL snapshots, связанные с запуском, исключены из очистки staged/orphan результатов. Квоты object storage продолжают действовать; долгоживущие данные запусков учитывайте при планировании ёмкости. Исполнение пользовательских операций не получает неограниченных повторов: ошибки прав, схемы и пользовательского кода завершают шаг. При сбое сначала проверяются сохранённая операция и результат. Для запроса к внешнему LLM без сохранённого ответа возможна новая попытка; стоимость такого запроса зависит от провайдера.

## API

Черновик: `GET/PUT /v1/scenarios/{id}/draft`. Публикация: `POST /v1/scenarios/{id}/publish` с `expected_revision`. Версии: `GET /v1/scenarios/{id}/versions`. Проверка и AI-предложения: `/validate`, `/ai-proposals`.

`POST /v1/scenarios/{id}/runs` принимает `Idempotency-Key` и JSON с `workspace_id`, `inputs`, необязательными `version` и `conversation_id`. Ответ `202` содержит `run_id`. Тест черновика использует `draft_revision`; тест одного шага — дополнительно `test_node_id` и `test_inputs`.

`GET /v1/scenario-runs` фильтруется по сценарию или разговору. Детали: `GET /v1/scenario-runs/{run_id}`; SSE: `/events`; отмена: `POST /cancel`; решение: `POST /steps/{node_id}/approval` с `approved`. Доступ к запуску имеет инициатор. Старый синхронный `/v1/scenarios/{id}/run` работает для legacy и требует асинхронный API для workflow.
