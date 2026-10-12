FROM python:3.12-slim AS base
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_INDEX=${PIP_INDEX_URL} \
    UV_DEFAULT_INDEX=https://pypi.org/simple \
    UV_INDEX_STRATEGY=first-index \
    UV_INSECURE_HOST=${PIP_TRUSTED_HOST} \
    UV_HTTP_RETRIES=10 \
    UV_HTTP_TIMEOUT=300
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
      --index-url "${PIP_INDEX_URL}" \
      --trusted-host "${PIP_TRUSTED_HOST}" \
      uv \
    || pip install --no-cache-dir uv
WORKDIR /workspace

FROM base AS dev
COPY apps/worker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]

FROM base AS prod
ENV WORKER_APP_ENV=prod \
    LOG_LEVEL=INFO \
    LOG_LLM_CONTENT=false \
    RHETORICA_SYNC_ON_START=false \
    UV_PROJECT_ENVIRONMENT=/workspace/.venv
COPY . /workspace
RUN uv sync --frozen --package rhetorica-worker --no-dev
COPY apps/worker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]
