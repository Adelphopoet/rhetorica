# agent-kit: rhetorica-kit для Claude Code

Плагин Claude Code, который даёт человеку с токеном Rhetorica доступ к данным и скилл аналитика без git и без агентского хода Rhetorica.

```
plugin/
  .claude-plugin/plugin.json   манифест, userConfig.token (PAT, хранится в Keychain)
  .mcp.json                    MCP-сервер rhetorica, токен подставляется в Authorization
  skills/analyst/              метод: services → search_tables → run_sql → отчёт → база знаний
  skills/start/                /rhetorica-kit:start — проверка и первый пример
build.sh                       zip + marketplace.json в apps/web/public/install
```

## Как раздаётся

`build.sh` кладёт `rhetorica-kit.zip` и `marketplace.json` в `apps/web/public/install/`; web отдаёт их статикой по `/install/`. Скрипт запускается как `prebuild` web-приложения, поэтому образ web всегда содержит актуальный набор. Папка `public/install` в `.gitignore`.

Пользователь ставит без git:

```
claude plugin marketplace add https://rhetorica.example.com/install/marketplace.json
claude plugin install rhetorica-kit@rhetorica
```

Для окружения с другим адресом сборке передаётся `KIT_BASE_URL`.

## Локальная проверка

```
claude plugin marketplace add tools/agent-kit          # marketplace-dev.json рядом ссылается на ./plugin
claude plugin install rhetorica-kit@rhetorica-dev --config token=rh_pat_…
claude -p "/rhetorica-kit:start"
```

Разовая проба без установки: `claude --plugin-dir tools/agent-kit/plugin`.

## Правки

Скиллы — обычные `SKILL.md`, версия в `plugin.json` поднимается при любом изменении содержимого, иначе у клиентов не обновится.
