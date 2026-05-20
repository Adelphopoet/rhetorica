# Worker Runtime

Thin Celery runtime for async jobs triggered by the platform.

## Responsibilities

- queue bootstrap
- worker startup
- task routing
- job-specific observability hooks

Shared job logic belongs in `packages/backend`.
