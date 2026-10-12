---
name: start
description: Проверка подключения rhetorica-kit и первый пример. Без аргумента показывает коннекторы, базу знаний и how-to; с аргументом (тема) проходит цикл поиск → SQL → отчёт.
disable-model-invocation: true
argument-hint: "[тема, например: заказы]"
---

# /rhetorica-kit:start $ARGUMENTS

Работать по скиллу `analyst` этого же плагина. Никаких `ask_chat`.

## Без аргумента

1. `list_connectors` на сервере `rhetorica`. Нет ответа или 401 → сказать, что токен не подходит, и показать, где взять новый: Rhetorica → Settings → API & MCP tokens, scopes `mcp:read` и `mcp:execute`; вставить через `/plugin` → rhetorica-kit → Configure. Дальше не идти.
2. `list_metadata_services`. Если OpenMetadata недоступна, показать её model-readable ошибку, но не объявлять всё подключение сломанным: runtime-поиск через database connectors остаётся доступен.
3. Найти базу знаний по шагу 0 скилла `analyst`; если нет, предложить создать.
4. Напечатать коротко:

```
Rhetorica подключена: <n> коннекторов (<имена>).
OpenMetadata: <m> services (<имена>) | недоступна: <ошибка>.
База знаний: <путь> (<k> таблиц, <m> запросов) | ещё не создана.

Как пользоваться
- Спрашивай про данные обычным языком, скилл analyst сам найдёт таблицу и напишет SQL.
- Знаешь таблицу — назови её, поиск пропустится.
- Каждый ответ уходит в <база>/reports/<slug>/, а что узнал о таблицах — в knowledge/.
- Пример цикла целиком: /rhetorica-kit:start заказы
- Токен протух: /plugin → rhetorica-kit → Configure.
```

SQL без аргумента не выполнять.

## С аргументом

Аргумент — тема. Пройти цикл и показать каждый шаг одной строкой:

1. `list_metadata_services` и `list_connectors` → выбрать точные `service_name` и `connector_name`; при неоднозначной связи спросить человека.
2. `search_tables(query=<тема>, service_name=..., connector_name=...)` → выбрать лучшего кандидата, объяснить почему (`origin`, `found_by`, описание, слой, статус источников).
3. `run_sql("DESCRIBE <таблица>", connector_name=...)` → найти колонку даты.
4. Один запрос: число строк по дням за последние 7 дней по этой колонке, `LIMIT 10`.
5. Отчёт в `reports/start-<тема латиницей>-<YYYY-MM-DD>/` (slug только латиницей, kebab-case), строка в `knowledge/tables.md`, запрос в `knowledge/sql-library.md`.
6. Ответ по формату из `analyst`, в конце путь к отчёту и напоминание, что следующий вопрос можно задавать без команды.
