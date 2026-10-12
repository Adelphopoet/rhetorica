FROM python:3.12.12-slim-bookworm AS build
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org
ARG DBT_CORE_VERSION=1.11.15
ARG DBT_TRINO_VERSION=1.10.5
ARG PYTEST_VERSION=8.3.5
ENV UV_PROJECT_ENVIRONMENT=/opt/rhetorica-venv \
    UV_INDEX=${PIP_INDEX_URL} \
    UV_DEFAULT_INDEX=https://pypi.org/simple \
    UV_INDEX_STRATEGY=first-index \
    UV_INSECURE_HOST=${PIP_TRUSTED_HOST} \
    UV_HTTP_RETRIES=10 \
    UV_HTTP_TIMEOUT=300
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
       --trusted-host "${PIP_TRUSTED_HOST}" uv
WORKDIR /workspace
COPY . /workspace
RUN uv sync --frozen --package rhetorica-coding-runner --no-dev --no-editable \
    && uv pip install --python /opt/rhetorica-venv/bin/python --no-cache \
       --index-url "${PIP_INDEX_URL}" \
       "dbt-core==${DBT_CORE_VERSION}" "dbt-trino==${DBT_TRINO_VERSION}" "pytest==${PYTEST_VERSION}" \
    && printf '{"python":"3.12.12","dbt-core":"%s","dbt-trino":"%s","pytest":"%s"}\n' \
       "${DBT_CORE_VERSION}" "${DBT_TRINO_VERSION}" "${PYTEST_VERSION}" >/opt/software-manifest.json

FROM python:3.12.12-slim-bookworm AS prod
ENV PATH=/opt/rhetorica-venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    GIT_CONFIG_NOSYSTEM=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git passwd ripgrep tini \
    && rm -rf /var/lib/apt/lists/* \
    && /usr/sbin/groupadd --gid 65532 coding \
    && /usr/sbin/useradd --uid 65532 --gid 65532 --home-dir /workspace --shell /usr/sbin/nologin coding \
    && mkdir -p /workspace /scratch \
    && chown 65532:65532 /workspace /scratch
COPY --from=build /opt/rhetorica-venv /opt/rhetorica-venv
COPY --from=build /opt/software-manifest.json /opt/software-manifest.json
WORKDIR /workspace
USER 65532:65532
EXPOSE 8091
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "rhetorica_coding_runner.app:app", "--host", "0.0.0.0", "--port", "8091"]
