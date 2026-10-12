# Web Runtime

Feature-oriented Next.js app for the analyst workspace.

## Zones

- chat
- SQL inspector
- results
- visualization
- workspace navigation

## Тема интерфейса

Кнопка переключает светлую и тёмную тему одним нажатием. Последний выбор хранится в браузере и синхронизируется между его вкладками. По умолчанию используется тёмная тема; системная палитра на выбор не влияет.

Семантические цвета определены в `src/app/globals.css` и доступны общим компонентам через Tailwind. Модуль `src/features/theme` управляет предпочтением и ранним применением палитры до гидратации. ECharts, Mermaid, Monaco и ReactFlow используют ту же тему, сохраняя состояние при переключении.

Контракт: FR-RH-019, NFR-RH-009 и UC-RH-14 в `openspec/specs`. Тесты входят в существующий набор Vitest: `pnpm test`. Проверки перед MR: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`.
