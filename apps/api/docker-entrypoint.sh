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
  uv sync --package rhetorica-api --all-extras
else
  UV_RUN_ARGS="--no-sync"
fi

# Run migrations
echo "Running database migrations..."
uv run $UV_RUN_ARGS python -c "from rhetorica_backend.infra.postgres.migrations import run_migrations; run_migrations()"

# Start the application
echo "Starting API server..."
export API_APP_ENV="${API_APP_ENV:-dev}"
export LOG_LEVEL="${LOG_LEVEL:-DEBUG}"
export LOG_LLM_CONTENT="${LOG_LLM_CONTENT:-true}"
UVICORN_ARGS="--host 0.0.0.0 --port 8000 --access-log --log-level ${UVICORN_LOG_LEVEL:-debug}"

if [ "${UVICORN_RELOAD:-false}" = "true" ]; then
  UVICORN_ARGS="$UVICORN_ARGS --reload"
fi

uv run $UV_RUN_ARGS uvicorn rhetorica_api.app.main:app $UVICORN_ARGS
