COMPOSE := docker compose
COMPOSE_FILE := compose.yaml
APP_PROFILE := --profile apps
DATABASE_URL_LOCAL ?= postgresql+psycopg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:$(POSTGRES_PORT)/$(POSTGRES_DB)

-include .env

.PHONY: bootstrap infra-up infra-down apps-up apps-down dev-up dev-down dev-down-clean logs ps uv-sync uv-lock web-install compose-config test test-parallel
.PHONY: wait-postgres db-migrate db-migrate-make db-downgrade db-current db-heads
.PHONY: gitlab-publisher-up gitlab-publisher-down gitlab-publisher-live

bootstrap:
	cp -n .env.example .env || true

infra-up:
	$(COMPOSE) -f $(COMPOSE_FILE) up -d postgres redis qdrant minio minio-init

infra-down:
	$(COMPOSE) -f $(COMPOSE_FILE) stop postgres redis qdrant minio minio-init

apps-up:
	$(COMPOSE) -f $(COMPOSE_FILE) $(APP_PROFILE) up -d api worker web coding-query-gateway

apps-down:
	$(COMPOSE) -f $(COMPOSE_FILE) $(APP_PROFILE) stop api worker web coding-query-gateway

dev-up:
	$(COMPOSE) -f $(COMPOSE_FILE) $(APP_PROFILE) up -d --build

dev-down:
	$(COMPOSE) -f $(COMPOSE_FILE) $(APP_PROFILE) down --remove-orphans

dev-down-clean:
	$(COMPOSE) -f $(COMPOSE_FILE) $(APP_PROFILE) down --remove-orphans -v

logs:
	$(COMPOSE) -f $(COMPOSE_FILE) logs -f --tail=200

ps:
	$(COMPOSE) -f $(COMPOSE_FILE) ps

uv-sync:
	uv sync --all-packages --all-groups

uv-lock:
	uv lock

web-install:
	npx pnpm@10.0.0 install --lockfile-only

compose-config:
	$(COMPOSE) -f $(COMPOSE_FILE) config >/dev/null

test:
	uv run --all-packages --all-extras pytest
	pnpm test

test-parallel:
	uv run --all-packages --all-extras pytest -n auto --dist loadfile
	pnpm test

# Database migrations
wait-postgres:
	@until $(COMPOSE) -f $(COMPOSE_FILE) exec -T postgres pg_isready -U "$(POSTGRES_USER)" -d "$(POSTGRES_DB)" >/dev/null 2>&1; do \
		echo "Waiting for postgres..."; \
		sleep 1; \
	done

db-migrate: wait-postgres
	cd packages/backend && DATABASE_URL="$(DATABASE_URL_LOCAL)" uv run python -c "from rhetorica_backend.infra.postgres.migrations import run_migrations; run_migrations()"

db-migrate-make: wait-postgres
	@read -p "Enter migration message: " message; \
	cd packages/backend && DATABASE_URL="$(DATABASE_URL_LOCAL)" uv run alembic revision --autogenerate -m "$$message"

db-downgrade: wait-postgres
	cd packages/backend && DATABASE_URL="$(DATABASE_URL_LOCAL)" uv run python -c "from rhetorica_backend.infra.postgres.migrations import downgrade_migrations; downgrade_migrations()"

db-current: wait-postgres
	cd packages/backend && DATABASE_URL="$(DATABASE_URL_LOCAL)" uv run alembic current

db-heads:
	cd packages/backend && DATABASE_URL="$(DATABASE_URL_LOCAL)" uv run alembic heads
