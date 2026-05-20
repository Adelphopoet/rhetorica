.PHONY: sync lint test test-parallel run

sync:
	uv sync --package rhetorica-api --package rhetorica-backend --all-groups

lint:
	uv run ruff check apps/api/src packages/backend/src
	uv run mypy

test:
	uv run --all-packages --all-extras pytest apps/api/tests packages/backend/tests

test-parallel:
	uv run --all-packages --all-extras pytest apps/api/tests packages/backend/tests -n auto --dist loadfile

run:
	uv run uvicorn rhetorica_api.app.main:app --host 0.0.0.0 --port 8000 --reload
