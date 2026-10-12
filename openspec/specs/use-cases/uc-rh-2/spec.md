---
title: "Выполнить проверяемый SQL"
linkTitle: UC-RH-2
weight: 2
url: /docs/rhetorica/uc/uc-rh-2/
openspec_kind: UC
requirement_root: UC-RH-2
---

# UC-RH-2: Выполнить проверяемый SQL

## Purpose

Зафиксировать основной вариант использования и его наблюдаемый результат.

## Requirements

### Requirement: UC-RH-2 — Выполнить проверяемый SQL

<a id="uc-rh-2"></a>
Система SHALL поддерживать безопасное read-only выполнение с отслеживаемым статусом.

**Основной актор:** аналитик.

**Цель:** получить результат конкретного SQL и проверить его исполнение.

#### Scenario: Основной успешный сценарий

- **WHEN** аналитик передаёт один разрешённый read-only SQL и выбирает доступный DWH-коннектор
- **THEN** система выполняет guard и preflight, создаёт execution и возвращает результат со статусом и provenance
