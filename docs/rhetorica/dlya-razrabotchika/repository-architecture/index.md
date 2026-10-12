---
title: Устройство репозитория
linkTitle: Устройство репозитория
weight: 20
---

# Устройство репозитория

```text
apps/
  api/         FastAPI composition root и HTTP routes
  worker/      Celery runtime
  web/         Next.js интерфейс
  sandbox/     изолированная обработка файлов
packages/
  backend/     домены, contracts, orchestration и инфраструктурные adapters
  ui/          общие UI-компоненты
deploy/helm/   Helm chart
openspec/      канонические требования и change workflow
docs/
  rhetorica/   публикуемая документация
  archive/     внутренние ADR и legacy/generated материалы
```

## Правила зависимостей

- `apps/api` и `apps/worker` только собирают зависимости и транспорт.
- Бизнес-логика живёт рядом с доменом в `packages/backend`.
- Orchestrator координирует collaborators, но не содержит SQL policy, metadata guessing и response repair.
- Наблюдаемое изменение сопровождается OpenSpec delta, тестами и обновлением публичной документации.
- Архитектурные решения фиксируются в `docs/archive/architecture/adr`; этот раздел намеренно не публикуется пользователям.
