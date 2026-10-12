---
title: "Детерминированный отказ для опасного SQL"
linkTitle: NFR-RH-001
weight: 1
url: /docs/rhetorica/nfr/nfr-rh-001/
openspec_kind: NFR
requirement_root: NFR-RH-001
---

# NFR-RH-001: Read-only SQL safety

## Purpose

Зафиксировать измеримое ограничение качества соответствующей capability Rhetorica.

## Requirements

### Requirement: NFR-RH-001 — Детерминированный отказ для опасного SQL

<a id="nfr-rh-001"></a>
Система MUST отклонять запрещённый SQL до обращения к DWH.

**Use Cases:** UC-RH-2

**Метрика:** доля тестовых DML, DDL и multi-statement запросов, отклонённых SQL guard до вызова execution adapter.

**Порог:** 100% набора policy-тестов.

**Условия измерения:** CI, поддерживаемые SQL dialects, прямой guard API и chat execution path.

**Проверка:** unit и integration tests пакета `rhetorica_backend.sql_guard` и execution orchestration.

#### Scenario: Опасный запрос попал в guard

- **WHEN** проверочный набор передаёт SQL с изменением данных или несколькими операторами
- **THEN** каждый запрос отклоняется до единственного вызова DWH adapter
