---
title: Локальная разработка
linkTitle: Локальная разработка
weight: 10
---

# Локальная разработка

## Требования

- Docker и Docker Compose;
- Python 3.12 из `.python-version` для host-side `uv`;
- Node.js 22 и pnpm через Corepack;
- `uv`, `make`, Git.

## Docker-first запуск

```bash
cp .env.example .env
make bootstrap
make infra-up
make db-migrate
make dev-up
```

Остановка и логи:

```bash
make logs
make dev-down
```

## Проверки

```bash
uv run --all-packages --all-extras pytest -n auto --dist loadfile
pnpm lint
pnpm typecheck
pnpm test
pnpm build
helm lint deploy/helm/rhetorica
```

Секреты остаются в `.env`, локальном encrypted store или внешнем backend. Не добавляйте их в fixtures и документацию.
