---
title: "Проверить результат разработки"
linkTitle: UC-RH-18
weight: 18
url: /docs/rhetorica/uc/uc-rh-18/
openspec_kind: UC
requirement_root: UC-RH-18
---

# UC-RH-18 — Проверить результат разработки

## Purpose

Дать ревьюеру проверяемые сведения о точной версии изменений и результатах QA перед самостоятельным решением о merge в GitLab.

## Requirements

### Requirement: UC-RH-18 — Проверить результат разработки

<a id="uc-rh-18"></a>
Система SHALL предоставлять ревьюеру diff и результаты проверки опубликованного candidate перед самостоятельным merge в GitLab.

**Основной актор:** ревьюер.

**Цель:** Оценить точный diff и достаточность проверки перед merge.

**Основной сценарий:**

1. Ревьюер открывает доступный coding run.
2. Сверяет base SHA, candidate digest, diff и QA-report.
3. Проверяет not_run для materialization и другие ограничения.
4. Переходит в Draft MR с тем же изменением.
5. Принимает решение о merge средствами GitLab; Rhetorica merge не выполняет.

#### Scenario: Проверить результат разработки

- **WHEN** ревьюер открывает завершённый coding run и переходит в Draft MR
- **THEN** ревьюер видит исходный commit, проверенный diff, результаты QA и непроверенные materialization-проверки; merge выполняет ревьюер

#### Scenario: Ревьюер открывает завершённый run

- **WHEN** ревьюер открывает доступный завершённый coding run с созданным Draft MR
- **THEN** система показывает base SHA, candidate digest, diff, QA statuses и `not_run` ограничения, а merge ревьюер выполняет средствами GitLab
