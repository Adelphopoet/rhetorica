---
title: "P95 latency quality gate"
linkTitle: NFR-RH-004
weight: 4
url: /docs/rhetorica/nfr/nfr-rh-004/
openspec_kind: NFR
requirement_root: NFR-RH-004
---

# NFR-RH-004: Задержка аналитического ответа

## Purpose

Зафиксировать измеримое ограничение качества соответствующей capability Rhetorica.

## Requirements

### Requirement: NFR-RH-004 — P95 latency quality gate

<a id="nfr-rh-004"></a>
Система MUST контролировать задержку аналитического runtime на закреплённом eval-наборе.

**Use Cases:** UC-RH-1, UC-RH-8

**Метрика:** `latency_p95` в миллисекундах.

**Порог:** не более 9000 мс.

**Условия измерения:** тот же CI eval-набор и runtime profile, которые заданы в `quality-gate-policy.v1.json`.

**Проверка:** `scripts/run_llm_quality_gates.py` сравнивает измерение с max policy и возвращает ненулевой exit code при превышении.

#### Scenario: P95 превышает gate

- **WHEN** измеренный `latency_p95` больше 9000 мс
- **THEN** quality-gate job завершается ошибкой и показывает нарушенную метрику
