---
title: "Минимальные quality gates"
linkTitle: NFR-RH-003
weight: 3
url: /docs/rhetorica/nfr/nfr-rh-003/
openspec_kind: NFR
requirement_root: NFR-RH-003
---

# NFR-RH-003: Качество аналитического ответа

## Purpose

Зафиксировать измеримое ограничение качества соответствующей capability Rhetorica.

## Requirements

### Requirement: NFR-RH-003 — Минимальные quality gates

<a id="nfr-rh-003"></a>
Система MUST проходить закреплённые offline quality gates перед выпуском изменения аналитического runtime.

**Use Cases:** UC-RH-8

**Метрика:** `answer_correctness`, `sql_correctness`, `execution_success_rate`, `grounded_claim_ratio` на зафиксированном eval-наборе.

**Порог:** соответственно не ниже 0.75, 0.70, 0.70 и 0.70.

**Условия измерения:** CI запускает `scripts/run_llm_quality_gates.py` с `smoke_core_samples.json` и `quality-gate-policy.v1.json`.

**Проверка:** job `llm-quality-gates` завершается успешно и публикует фактические метрики.

#### Scenario: Качество ниже порога

- **WHEN** хотя бы одна минимизируемая метрика eval-набора ниже значения policy
- **THEN** quality-gate job завершается ошибкой и изменение не считается прошедшим проверку
