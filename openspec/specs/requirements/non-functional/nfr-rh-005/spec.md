---
title: "Полный frontend quality job"
linkTitle: NFR-RH-005
weight: 5
url: /docs/rhetorica/nfr/nfr-rh-005/
openspec_kind: NFR
requirement_root: NFR-RH-005
---

# NFR-RH-005: Проверка frontend

## Purpose

Зафиксировать измеримое ограничение качества соответствующей capability Rhetorica.

## Requirements

### Requirement: NFR-RH-005 — Полный frontend quality job

<a id="nfr-rh-005"></a>
Система MUST проверять lint, типы, unit tests и production build web-приложения в одном обязательном CI job для merge request.

**Use Cases:** UC-RH-8

**Метрика:** доля merge-request pipelines, в которых job `web-quality` выполнил все четыре проверки.

**Порог:** 100% merge-request pipelines, допущенных к merge.

**Условия измерения:** GitLab CI для merge request в репозитории Rhetorica с frozen `pnpm-lock.yaml`.

**Проверка:** job последовательно выполняет `pnpm lint`, `pnpm typecheck`, `pnpm test` и `pnpm build`; ошибка любой команды завершает job.

#### Scenario: TypeScript-контракт нарушен

- **WHEN** изменение вызывает ошибку `pnpm typecheck`
- **THEN** `web-quality` завершается ошибкой и merge request не проходит frontend gate
