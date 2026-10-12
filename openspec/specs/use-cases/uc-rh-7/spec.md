---
title: "Настроить агента и skills"
linkTitle: UC-RH-7
weight: 7
url: /docs/rhetorica/uc/uc-rh-7/
openspec_kind: UC
requirement_root: UC-RH-7
---

# UC-RH-7: Настроить агента и skills

## Purpose

Зафиксировать основной вариант использования и его наблюдаемый результат.

## Requirements

### Requirement: UC-RH-7 — Настроить агента и skills

<a id="uc-rh-7"></a>
Система SHALL позволять системному администратору настроить профиль агента или workspace default assistant из явных instructions, skills, connector allowlists, direct write-scopes, разрешённых субагентов, delegation grants и runtime limits.

**Основной актор:** системный администратор.

**Цель:** опубликовать специализированного агента с понятными, закрытыми по умолчанию и конкурентно безопасными границами доступа.

#### Scenario: Основной успешный сценарий

- **WHEN** системный администратор с актуальной revision импортирует `trino-guide` из разрешённого GitLab connector по commit SHA, назначает skill developer и QA, включает отдельный skill default assistant и сохраняет профиль
- **THEN** система возвращает новую revision, показывает каждое назначение, provenance и manifest файлов, а новые ходы используют только назначенные skills

#### Scenario: Настройка делегирования без direct write родителя

- **WHEN** системный администратор оставляет direct write default assistant пустым, добавляет developer в allowlist и отдельно задаёт edge grant `default assistant → developer` для `analytics/dbt/main/models/`
- **THEN** UI показывает раздельные полномочия, default assistant может поручить developer публикацию в указанном scope и не может публиковать самостоятельно

#### Scenario: Конфликт параллельного редактирования

- **WHEN** другой администратор сохранил более новую policy revision после открытия формы
- **THEN** система не перезаписывает новую политику, сообщает конфликт и предлагает редактору загрузить актуальное состояние
