# Security Baseline

## 1. Модель безопасности

Rhetorica проектируется как governed analytics system. Базовый принцип: **read-only analytics by default**.

## 2. Минимальные обязательные контроли

- Read-only credentials для DWH подключений.
- SQL guard перед execution.
- Лимиты `timeout_ms`, `row_limit`, `max_concurrency`.
- История execution со статусами, warnings и violations.
- Redaction чувствительных полей в connection summary.

## 3. Уже реализовано

- SQL parse/validation через SQLGlot.
- Блокировка DDL/DML и multi-statement.
- Clamp/forced `LIMIT` по policy.
- Persist execution metadata в PostgreSQL.

## 4. Что должно быть добавлено в следующих итерациях

- Шифрование секретов с внешним KMS/Vault.
- Schema/catalog allowlist policy.
- Workspace-level RBAC.
- Аудит критичных admin-действий.
- Централизованный security observability pipeline.

## 5. Anti-patterns

- Прямой execute без guard.
- Shared admin credentials для всех workspaces.
- Скрытый SQL от аналитика.
- Игнорирование cost controls ради «быстрее ответить».

