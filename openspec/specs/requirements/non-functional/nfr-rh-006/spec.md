---
title: "Read-only источник данных о ролях"
linkTitle: NFR-RH-006
weight: 6
url: /docs/rhetorica/nfr/nfr-rh-006/
openspec_kind: NFR
requirement_root: NFR-RH-006
---

# NFR-RH-006: Read-only источник данных о ролях

## Purpose

Зафиксировать измеримое ограничение качества соответствующей capability Rhetorica.

## Requirements

### Requirement: NFR-RH-006 — Read-only источник данных о ролях

<a id="nfr-rh-006"></a>
Система MUST получать данные о ролях из Teradata только на чтение, используя представления DBC.ROLEMEMBERSV и TERADATA.DB_Admin.V_GRANTS, без операций записи.

**Use Cases:** UC-RH-9

**Метрика:** доля запросов к Teradata, выполненных только на чтение.

**Порог:** 100% запросов.

**Условия измерения:** сценарии проверки доступа и сравнения доступов.

**Проверка:** unit-тесты слоя работы с данными о ролях и аудит SQL-запросов.

#### Scenario: Запрос ролей

- **WHEN** система обращается к DBC.ROLEMEMBERSV или TERADATA.DB_Admin.V_GRANTS
- **THEN** запрос выполняется только на чтение
