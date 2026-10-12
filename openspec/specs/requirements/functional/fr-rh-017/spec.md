---
title: "Политика агента в MCP-чате"
linkTitle: FR-RH-017
weight: 17
url: /docs/rhetorica/fr/fr-rh-017/
openspec_kind: FR
requirement_root: FR-RH-017
---

# FR-RH-017 — Политика агента в MCP-чате

## Purpose

Обеспечить внешнему MCP-клиенту управляемый выбор workspace-агента и многоходовый диалог с неизменной политикой выбранного агента.

## Requirements

### Requirement: FR-RH-017 — Политика агента в MCP-чате

<a id="fr-rh-017"></a>
Система SHALL выполнять каждый MCP chat turn с prompt, runtime settings и ограничениями ресурсов агента, закреплённого за conversation.

**Use Cases:** UC-RH-11

#### Scenario: Новый диалог с выбранным агентом

- **WHEN** аутентифицированный MCP-клиент вызывает chat tool с идентификатором активного агента своего workspace
- **THEN** система создаёт conversation с этим агентом, применяет его настройки к ответу и возвращает идентификаторы conversation и агента

```plantuml
@startuml
actor "Пользователь" as User
participant "Внешний MCP-клиент" as Client
participant "Rhetorica MCP" as MCP

User -> Client: Выбрать агента и задать вопрос
Client -> MCP: list_agents
MCP --> Client: Активные агенты workspace
Client -> MCP: ask_chat(question, agent_id)
alt агент доступен
  MCP --> Client: Ответ, conversation_id и agent_id
  Client -> MCP: ask_chat(follow-up, conversation_id)
  MCP --> Client: Ответ того же агента
else агент недоступен
  MCP --> Client: Структурированная ошибка
end
@enduml
```

### Requirement: FR-RH-017.1 — Каталог агентов MCP

<a id="fr-rh-017-1"></a>
Система SHALL предоставлять через read-only MCP tool каталог активных агентов workspace с идентификатором, названием, описанием и безопасными признаками доступных возможностей без раскрытия admin prompt и секретов коннекторов.

**Use Cases:** UC-RH-11

#### Scenario: Клиент обнаруживает агентов

- **WHEN** аутентифицированный MCP-клиент запрашивает каталог агентов
- **THEN** система возвращает только активных агентов workspace владельца токена и model-readable metadata для осознанного выбора

### Requirement: FR-RH-017.2 — Закрепление агента за conversation

<a id="fr-rh-017-2"></a>
Система SHALL закреплять выбранного агента за новой MCP conversation и SHALL восстанавливать этого агента для последующих turn по `conversation_id`, когда клиент не повторяет `agent_id`.

**Use Cases:** UC-RH-11

#### Scenario: Клиент продолжает диалог

- **WHEN** MCP-клиент отправляет follow-up с `conversation_id` созданного agent-aware диалога без `agent_id`
- **THEN** система выполняет turn с агентом, закреплённым за conversation, и возвращает его `agent_id`

#### Scenario: Клиент пытается сменить агента

- **WHEN** MCP-клиент передаёт для существующей conversation `agent_id`, отличный от закреплённого
- **THEN** система отклоняет вызов со структурированным кодом `conversation_agent_locked` и не добавляет сообщения

### Requirement: FR-RH-017.3 — Изоляция agent-aware MCP-чата

<a id="fr-rh-017-3"></a>
Система SHALL отклонять выбор отсутствующего или неактивного агента и доступ к conversation другого пользователя либо workspace до запуска chat turn.

**Use Cases:** UC-RH-11

#### Scenario: Агент недоступен владельцу токена

- **WHEN** MCP-клиент передаёт `agent_id`, который отсутствует, неактивен или принадлежит другому workspace
- **THEN** система возвращает структурированный код `agent_not_found` без раскрытия metadata агента

#### Scenario: Conversation недоступна владельцу токена

- **WHEN** MCP-клиент передаёт `conversation_id`, не принадлежащий владельцу токена в его workspace
- **THEN** система возвращает структурированный код `conversation_not_found` и не запускает chat turn
