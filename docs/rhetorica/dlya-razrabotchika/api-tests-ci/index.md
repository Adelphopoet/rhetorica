---
title: API, тесты и CI
linkTitle: API, тесты и CI
weight: 30
---

# API, тесты и CI

FastAPI публикует OpenAPI и Swagger в runtime по `/openapi.json` и `/docs`. Публичная продуктовая документация живёт отдельно в GitLab Pages.

Основные API-группы: auth, agents, chat, SQL guard/executions, files, scenarios и admin connectors. Новые routes остаются тонкими и делегируют use case в `packages/backend`.

## OpenSpec

```bash
npx --yes @fission-ai/openspec@1.7.0 validate --all --strict --no-interactive
python tools/sync_openspec_docsy.py --check
```

Для изменения поведения используйте propose → apply → archive. После обновления main specs выполните `python tools/sync_openspec_docsy.py --write`.

## CI

Merge request проверяет shape репозитория, Python workspace, frontend, Compose, Helm и LLM quality gates. Изменение `docs/rhetorica` или `openspec/specs` в `main` запускает pipeline `platform_docs`, который собирает Hugo Docsy и публикует GitLab Pages.
