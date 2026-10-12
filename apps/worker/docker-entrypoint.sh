#!/bin/sh
set -e

install_extra_cas() {
  EXTRA_CA_DIR="${RHETORICA_EXTRA_CA_DIR:-/workspace/.rhetorica/certs}"
  if [ ! -d "$EXTRA_CA_DIR" ]; then
    return
  fi

  found_certs=false
  for cert in "$EXTRA_CA_DIR"/*.crt; do
    if [ ! -f "$cert" ]; then
      continue
    fi
    found_certs=true
    cp "$cert" "/usr/local/share/ca-certificates/$(basename "$cert")"
  done

  if [ "$found_certs" = true ]; then
    echo "Installing extra CA certificates from $EXTRA_CA_DIR..."
    update-ca-certificates
  fi

  export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
  export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
}

install_extra_cas

UV_RUN_ARGS=""
if [ "${RHETORICA_SYNC_ON_START:-true}" = "true" ]; then
  uv sync --package rhetorica-worker --all-extras
else
  UV_RUN_ARGS="--no-sync"
fi

if [ "${WORKER_RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "Running database migrations..."
  uv run $UV_RUN_ARGS python -c "from rhetorica_backend.infra.postgres.migrations import run_migrations; run_migrations()"
else
  echo "Skipping database migrations in worker startup."
fi

# Start the worker
if [ "${RHETORICA_WORKER_KIND:-celery}" = "coding" ]; then
  exec uv run $UV_RUN_ARGS python -m rhetorica_worker.runtime.coding
fi
if [ "${RHETORICA_WORKER_KIND:-celery}" = "scenarios" ]; then
  exec uv run $UV_RUN_ARGS python -m rhetorica_worker.runtime.scenarios
fi

echo "Starting worker..."
export WORKER_APP_ENV="${WORKER_APP_ENV:-dev}"
export LOG_LEVEL="${LOG_LEVEL:-DEBUG}"
export LOG_LLM_CONTENT="${LOG_LLM_CONTENT:-true}"

CELERY_WORKER_ARGS=""
if [ "${CELERY_BEAT_ENABLED:-true}" = "true" ]; then
  CELERY_WORKER_ARGS="$CELERY_WORKER_ARGS --beat"
fi
if [ -n "${CELERY_WORKER_CONCURRENCY:-}" ]; then
  case "$CELERY_WORKER_CONCURRENCY" in
    *[!0-9]*)
      echo "CELERY_WORKER_CONCURRENCY must be a positive integer."
      exit 1
      ;;
    0)
      echo "CELERY_WORKER_CONCURRENCY must be greater than zero."
      exit 1
      ;;
    *)
      CELERY_WORKER_ARGS="$CELERY_WORKER_ARGS --concurrency=${CELERY_WORKER_CONCURRENCY}"
      ;;
  esac
fi

uv run $UV_RUN_ARGS celery -A rhetorica_worker.runtime.celery_app.celery_app worker --loglevel="${CELERY_LOG_LEVEL:-DEBUG}" $CELERY_WORKER_ARGS
