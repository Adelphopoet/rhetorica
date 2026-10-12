---
title: API и MCP-токены
linkTitle: API и MCP
weight: 50
description: Безопасное подключение OpenAI, Claude, MCP Inspector и других MCP-клиентов к Rhetorica.
---

# API и MCP-токены

Rhetorica публикует стандартный MCP Streamable HTTP endpoint `/mcp`. Внешний клиент работает от имени владельца токена и только в его workspace. `workspace_id` в аргументах tools не используется.

## Подключение через OAuth

Для клиента с поддержкой MCP Authorization достаточно указать публичный URL `https://rhetorica.example.com/mcp`. Клиент получает Protected Resource Metadata, открывает Rhetorica в браузере и просит подтвердить scopes. После подтверждения он получает короткоживущий access token и ротируемый refresh token; пароль и browser session клиенту не передаются.

На consent-экране проверьте имя клиента, адрес возврата и запрошенные права. `mcp:read` разрешает discovery и чтение, `mcp:execute` — сообщения агентам и выполнение инструментов. Для рабочего MCP lifecycle запрос `mcp:execute` автоматически включает `mcp:read`; consent и token metadata показывают оба effective scope. Отказывайте неизвестному клиенту или неожиданному redirect host.

Rhetorica использует Authorization Code с обязательным PKCE S256 и привязывает токены к точному `/mcp` resource. Client ID Metadata Document — основной способ регистрации hosted-клиента. Если deployment не может получить metadata hosted-клиента из своей сети, оператор отключает CIMD advertisement, и совместимый клиент использует Dynamic Client Registration. Повторное использование уже заменённого refresh token отзывает весь grant.

OAuth onboarding удобнее PAT: секрет не надо копировать вручную, access token живёт недолго, а refresh выполняется автоматически. PAT остаётся для скриптов и API-продуктов, которые принимают готовый bearer token, но сами не запускают OAuth.

## Выпуск токена

1. Откройте меню профиля и выберите **API & MCP tokens**.
2. Задайте понятное имя клиента.
3. Выберите срок: 90, 180 или 365 дней. Минимальный срок — 90 дней.
4. Выберите только необходимые scopes и создайте токен.
5. Сразу скопируйте секрет: повторно Rhetorica его не покажет.

Автоматическое продление выключено по умолчанию. Если его включить, успешное использование не чаще одного раза в сутки сдвигает `expires_at` на выбранный исходный срок. Отзыв токена прекращает доступ немедленно.

| Scope | Назначение |
|---|---|
| `mcp:read` | Подключение, discovery, чтение resources, prompts и истории |
| `mcp:execute` | Выполнение аналитических tools, включая governed SQL; автоматически включает `mcp:read` |
| `api:read` | Чтение `/v1` API |
| `api:write` | Изменение через `/v1` API |

Для обычного MCP-клиента достаточно `mcp:read` и `mcp:execute`. Отдельный токен для каждого клиента упрощает отзыв и аудит.

## Подключение клиента

URL endpoint показан рядом с новым токеном. Для локального Docker он равен `http://localhost:8000/mcp`; этот адрес подходит MCP Inspector и другим локальным клиентам. OpenAI Responses API и Claude Messages API вызывают MCP из облака, поэтому для них Rhetorica должна быть доступна по публичному HTTPS URL с доверенным сертификатом.

OpenAI Responses API принимает MCP tool такого вида:

```json
{
  "type": "mcp",
  "server_label": "rhetorica",
  "server_url": "https://rhetorica.example.com/mcp",
  "authorization": "rh_pat_...",
  "allowed_tools": [
    "list_agents",
    "list_connectors",
    "list_conversations",
    "get_conversation",
    "ask_chat",
    "run_sql"
  ],
  "require_approval": "always"
}
```

Responses API принимает уже полученный access token в `authorization`; сам API-вызов не является браузерным OAuth-клиентом. Получите token через совместимый MCP-клиент, например Quick OAuth Flow в MCP Inspector, либо используйте PAT. Для hosted UI, который умеет MCP discovery и browser consent, передавайте только MCP URL.

`allowed_tools` не импортирует лишний изменяющий tool и уменьшает объём discovery. Расширяйте список осознанно.

Для Claude API добавьте объект в `mcp_servers`:

```json
{
  "type": "url",
  "url": "https://rhetorica.example.com/mcp",
  "name": "rhetorica",
  "authorization_token": "rh_pat_..."
}
```

Claude Messages MCP connector также ожидает готовый bearer token: приложение отвечает за OAuth flow и refresh. В Managed Agents используйте OAuth credential/vault, если выбранный режим поддерживает управляемое обновление. Для ручной диагностики MCP Inspector способен пройти Rhetorica OAuth целиком.

В локальном MCP Inspector укажите `http://localhost:8000/mcp` и HTTP header `Authorization: Bearer rh_pat_...`. После подключения проверьте `tools/list`, затем начните с read-only tool. Для tools, способных вызвать вычисление или изменить состояние, сохраняйте подтверждение человеком на стороне клиента.

## Набор для Claude Code: `rhetorica-kit`

Если клиент — Claude Code, не собирайте подключение вручную: Rhetorica раздаёт готовый плагин по адресу `/install/`. Git и доступ к репозиторию не нужны, только токен.

```bash
claude plugin marketplace add https://rhetorica.example.com/install/marketplace.json
claude plugin install rhetorica-kit@rhetorica
```

При установке Claude Code спросит токен (`rh_pat_...`, scopes `mcp:read` и `mcp:execute`) и сохранит его в системном хранилище ключей. Заменить токен: `/plugin` → `rhetorica-kit` → Configure. Обновить набор: `claude plugin update rhetorica-kit`.

Что внутри:

- MCP-сервер `rhetorica` с вашим токеном: `search_tables`, `run_sql`, `list_connectors` и tools коннекторов workspace.
- Скилл `analyst`: находит витрину через `search_tables`, читает колонки, выполняет read-only SQL, отвечает в формате «Ответ, Интерпретация, SQL, Источник», сохраняет отчёт и пополняет базу знаний (`knowledge/tables.md`, `sql-library.md`, `business-context.md`). Перед ответом заглядывает в базу. Место базы задаёт переменная `ANALYTICS_KB`, по умолчанию `~/analytics-kb/`; папка совместима с Obsidian.
- Команда `/rhetorica-kit:start`: без аргумента проверяет подключение, показывает коннекторы, базу знаний и короткую инструкцию; с аргументом (`/rhetorica-kit:start заказы`) проходит цикл поиск → SQL → отчёт на выбранной теме.

Агентский ход Rhetorica (`ask_chat`) набор не использует: SQL пишет сам клиент, Rhetorica исполняет его под коннектором workspace и ведёт аудит. Исходники набора лежат в репозитории в `tools/agent-kit/`.

## Диалог с агентом

Сначала вызовите read-only tool `list_agents`. Он возвращает активных агентов workspace и безопасные признаки их возможностей, но не раскрывает admin prompt, настройки доступа и секреты коннекторов.

Для первого сообщения передайте выбранный `agent_id` в `ask_chat`:

```json
{
  "question": "Покажи динамику выручки по месяцам",
  "agent_id": "agent_...",
  "run_sql": true
}
```

В ответе сохраните `conversation_id`. Для продолжения передавайте его без `agent_id`:

```json
{
  "question": "А теперь разбей последний месяц по регионам",
  "conversation_id": "conversation_..."
}
```

Rhetorica восстановит prompt, модель, параметры, skills, разрешённые коннекторы и SQL policy закреплённого агента. Смена агента внутри существующей conversation запрещена и возвращает `conversation_agent_locked`. Недоступный агент возвращает `agent_not_found`, чужая или отсутствующая conversation — `conversation_not_found`. `list_conversations` и `get_conversation` возвращают фактический `agent_id`.

## Инструменты коннекторов workspace

Помимо собственных tools Rhetorica публикует на `/mcp` полный разрешённый каталог активных MCP-коннекторов вашего workspace — например, OpenMetadata, Jira, Confluence или GitLab, если администратор их подключил, синхронизировал и включил `Expose through external MCP`. Применяется та же административная policy `allowed_tool_names`, что и для внутреннего LLM: непустой список разрешает только перечисленные tools, пустой список означает unrestricted. Отдельно устанавливать эти MCP-серверы и хранить их credentials не нужно: вызов выполняет Rhetorica под credentials коннектора, а клиент видит только результат.

Имя такого tool состоит из префикса коннектора и имени удалённого tool: `openmetadata-mcp__search_metadata`. Схема аргументов берётся из синхронизированного каталога коннектора; аргументы, не соответствующие схеме, отклоняются до обращения к удалённому серверу с кодом `mcp_tool_arguments_invalid`. Для вызова любого tool коннектора нужен scope `mcp:execute`.

Успешный ответ содержит `connector_name`, `remote_tool_name` и результат удалённого сервера в поле `result`. Отключённый или скрытый из external MCP коннектор, запрещённый административной policy tool, tool вне каталога или ошибка удалённого сервера возвращаются как `isError=true` со структурированным кодом ошибки. Скрытый коннектор может оставаться доступным внутреннему LLM. Rhetorica консервативно помечает проксируемые tools как potentially destructive, пока для них нет доверенной административной классификации; MCP-клиент может запросить подтверждение даже для read-only tool.

Типичный путь без агента: клиент находит таблицу через tool OpenMetadata, пишет SQL и выполняет его через `run_sql`. `ask_chat` при этом остаётся доступным — выбирайте его, когда нужен готовый ответ агента, а tools коннекторов — когда оркестрирует ваш собственный клиент.

Список tools коннекторов возвращается полностью и может быть длинным: администратор ограничивает его через `allowed_tool_names` коннектора, а на стороне клиента используйте `allowed_tools` или отложенную загрузку схем.

## Поиск таблиц: `search_tables`

Если нужный OpenMetadata service неизвестен, сначала вызовите `list_metadata_services`. Tool возвращает полный список database services с точными `name`, `fqn`, `service_type` и компактным описанием. Передавайте выбранное `name` в `search_tables.service_name`; это не имя database connector — доступные для SQL коннекторы перечисляет `list_connectors`.

Если вопрос не содержит имени таблицы, вызывайте `search_tables` — это один вызов вместо серии поисков по OpenMetadata. Tool ищет каскадом: OpenMetadata в точном `service_name`, либо по типам движков workspace, затем по всем сервисам только без точного выбора, затем по именам колонок; runtime-каталоги (`system.tables` в ClickHouse с поиском по комментариям, `information_schema` в остальных) проверяются независимо. Аргументы: `query` (любой язык), необязательные `service_name`, `connector_name`, `database`, `size` (до 50). Пагинации нет: ответ является одним совместно ранжированным набором.

Ступень runtime выполняется всегда, результаты сливаются с OpenMetadata и ранжируются: точное имя из запроса, совпадения в имени и описании, наличие описания, слой схемы (`stg`, `imp_*`, `raw` идут ниже витрин). Русские слова ищутся по основе, а для имён таблиц к ним добавляются английские термины (`заказ` → `order`, `чек` → `cheque`, `фудтех` → `foodtech`), поэтому вопрос можно задавать по-русски.

Ответ компактный: у каждого кандидата `fqn`, `origin`, фактический OpenMetadata `service` либо runtime `connector`, `description` до 300 символов, до 10 колонок, `found_by`, `layer` и `suspicious`. Совпадение OpenMetadata `serviceType` с engine не считается доказательством, что таблица исполнима через конкретный connector; одноимённые таблицы из разных источников не схлопываются. Поле `tried` перечисляет число найденных, `status` и безопасную ошибку каждой ступени: недоступный источник не выглядит как честные ноль результатов.

Почему так: при проверке с внешним клиентом поиск в OpenMetadata по слову «orders» без фильтра выдавал сначала таблицы из `sandbox` и учебные схемы, а семантический поиск был отключён — клиент бросал OpenMetadata и шёл в `system.tables`. `search_tables` выполняет тот порядок, который агент Rhetorica и так предпочитает, внутри одного вызова.

Для подробностей о найденной таблице — колонки целиком, lineage, владельцы — используйте tools коннектора OpenMetadata (`openmetadata__get_entity_details`, `openmetadata__get_entity_lineage`). Если администратор задал `usage_description` коннектора, оно показывается в начале description каждого его tool.

## Совместимость авторизации

- Актуальные MCP-клиенты: discovery, browser consent, PKCE и refresh без копирования PAT.
- Клиенты прежних версий: Dynamic Client Registration и тот же Authorization Code flow.
- OpenAI Responses API и Claude Messages API: передача уже полученного OAuth access token либо PAT в поле авторизации.
- Закрытая локальная установка без публичного HTTPS: PAT; browser OAuth разрешён по HTTP только на loopback development URL.

## Переход со старого MCP

Старые самописные transports удалены до релиза. Если клиент был настроен раньше:

1. Замените endpoint `/mcp/messages`, `/mcp/sse` или MCP WebSocket на единый `/mcp`.
2. Уберите `workspace_id` из аргументов tools: workspace определяется владельцем токена.
3. Выпустите в профиле отдельный PAT со scopes `mcp:read` и `mcp:execute` либо переподключите клиент через OAuth.
4. Повторно выполните discovery; для нового диалога при необходимости выберите агента через `list_agents` и передайте его `agent_id` в `ask_chat`.

Старый browser-session JWT больше не принимается на `/mcp`. Tool `submit_analysis_snapshot` также удалён: для создания и продолжения истории используйте `ask_chat`. В SQL-результатах читайте строки и колонки из `preview.rows` и `preview.columns`; старые дубли `data` и `columns` больше не возвращаются. Конфигурации, продолжающие обращаться к удалённым routes, получают `404`.

## Безопасность и диагностика

- Не храните PAT в Git, chat history, browser storage или публичной конфигурации.
- Выбирайте минимальные scopes и включайте автоматическое продление только для управляемого клиента.
- При подозрении на утечку отзовите токен в профиле и создайте новый.
- `401` означает недействительный, просроченный или отозванный токен; `403` — недостаточный scope.
- `429` на Dynamic Client Registration означает, что источник превысил лимит регистрации либо заполнена общая ёмкость; повторите подключение после времени из `Retry-After`.
- Используйте только `/mcp`; отдельные message, SSE и WebSocket transports не публикуются.

Оператор задаёт публичные HTTPS URL через `RHETORICA_PUBLIC_URL` и `RHETORICA_PUBLIC_API_URL`, обязательные разные secrets через `AUTH_JWT_SECRET`, `AUTH_API_TOKEN_PEPPER` и `AUTH_OAUTH_TOKEN_PEPPER`, а допустимые Host и Origin для MCP — через `MCP_ALLOWED_HOSTS` и `MCP_ALLOWED_ORIGINS`. Fallback token peppers на JWT signing key отсутствует. Production с включённым MCP требует `AUTH_ENABLED=true`.

`AUTH_OAUTH_CIMD_ENABLED=true` рекламирует и разрешает Client ID Metadata Documents. Если Kubernetes egress не может читать metadata конкретного hosted-клиента, установите `false`: authorization metadata сохранит `registration_endpoint`, но перестанет рекламировать CIMD, поэтому клиент сможет выбрать DCR без неуспешного server-side запроса.

На одном issuer host API ingress обязан публиковать `/mcp`, `/.well-known/oauth-protected-resource*`, `/.well-known/oauth-authorization-server`, а также exact routes `/oauth/authorize`, `/oauth/register`, `/oauth/token`, `/oauth/revoke`. Не направляйте весь prefix `/oauth` в API: browser consent `/oauth/consent` принадлежит frontend. Consent API публикуется через `/v1/oauth`.

Публичный DCR ограничен через `AUTH_OAUTH_DCR_REGISTRATIONS_PER_SOURCE` (по умолчанию 30 регистраций одного сетевого источника в час) и `AUTH_OAUTH_DCR_MAX_CLIENTS` (10 000 клиентов). Недействительные transactions, codes и grants очищаются после `AUTH_OAUTH_RECORD_RETENTION_HOURS` (24 часа), неактивные DCR clients без связанных OAuth-записей — после `AUTH_OAUTH_DCR_INACTIVE_CLIENT_RETENTION_DAYS` (90 дней). Активная refresh token family сохраняется целиком для обнаружения replay.
