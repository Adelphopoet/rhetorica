# Runbook: RAG Insight Pipeline (Local)

## 1. Поднять инфраструктуру и приложения

```bash
cp -n .env.example .env
make dev-up
```

Проверить что сервисы живы:

```bash
docker compose ps
```

Ожидаем минимум: `api`, `worker`, `web`, `postgres`, `redis`, `qdrant`.

## 2. Создать коннекторы в админке

Открыть `http://localhost:3000/admin` и создать:
- LLM connector (любой OpenAI-compatible endpoint);
- Database/OpenMetadata по необходимости;

RAG connector в UI не настраивается: используется встроенный backend config из env (`QDRANT_URL` + `RAG_*`).

## 3. Прогнать сценарий useful -> pending

1. В analyst workspace задать вопрос и дождаться ответа ассистента.
2. Поставить feedback `Useful`.
3. Подождать 1-5 секунд (worker заберёт задачу `rag_insights.process_feedback`).
4. Открыть `Admin -> RAG Insights Review`.

Ожидаемо: новая запись в `pending`.
- В карточке review должен быть `article_markdown` (структурированный markdown вместо сырого текста).

Дополнительно для similarity/merge:
1. Сделать второй похожий вопрос в том же workspace.
2. Поставить `Useful` на второй ответ.

Ожидаемо:
- если LLM merge gate решил `should_update_existing=true`, обновится summary у уже существующего point;
- если `should_update_existing=false`, point останется прежним;
- в обоих случаях у существующего point увеличится `likes_count`.

## 4. Verify / Reject

В секции `RAG Insights Review`:
- `Verify` -> запись уходит из pending и получает `verification_status=verified`;
- `Reject` -> запись уходит из pending и получает `verification_status=rejected`.

## 5. Быстрая диагностика

- API логи:
```bash
docker compose logs -f api
```

- Worker логи:
```bash
docker compose logs -f worker
```

- Проверка Qdrant:
```bash
curl http://localhost:${QDRANT_PORT:-6333}/collections
```

Если chat-ответ приходит, но pending не появляется:
- проверить что feedback именно `useful`;
- проверить наличие активного RAG connector для workspace;
- проверить доступность LLM connector (LLM gate обязателен в Phase 1);
- проверить что ответ не отфильтрован hard gate (`unsafe/blocked warnings`).

Если второй похожий useful не увеличивает лайки:
- проверить что similarity threshold не слишком высокий (`extra_settings.merge_similarity_threshold`);
- проверить в логах worker причину результата (`already_linked_to_existing`, `linked_existing_like`, `merged_into_existing`).

Если LLM не использует RAG retrieval в чате:
- проверить что в system/user prompt виден флаг `rag_tool_available=true`;
- проверить что в tool-loop есть вызов `search_rag_insights` (по API/worker логам);
- убедиться, что в Qdrant есть `verified` или `pending` записи по текущему workspace.
