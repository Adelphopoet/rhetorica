FROM debian:bookworm-slim AS nsjail-build
ARG NSJAIL_REF=3.4
RUN apt-get update && apt-get install -y --no-install-recommends \
    bison build-essential ca-certificates flex git libcap-dev libnl-route-3-dev \
    libprotobuf-dev libseccomp-dev pkg-config protobuf-compiler \
    && git clone --depth 1 --branch "${NSJAIL_REF}" https://github.com/google/nsjail.git /src/nsjail \
    && make -C /src/nsjail -j"$(nproc)" \
    && strip /src/nsjail/nsjail

FROM python:3.12-slim AS runtime
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy \
    UV_INDEX=${PIP_INDEX_URL} UV_DEFAULT_INDEX=https://pypi.org/simple \
    UV_INDEX_STRATEGY=first-index UV_INSECURE_HOST=${PIP_TRUSTED_HOST} \
    UV_PROJECT_ENVIRONMENT=/opt/rhetorica-venv
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcap2 libnl-route-3-200 libprotobuf32 libseccomp2 tini \
    && rm -rf /var/lib/apt/lists/* \
    && (pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" uv || pip install --no-cache-dir uv)
COPY --from=nsjail-build /src/nsjail/nsjail /usr/local/bin/nsjail
WORKDIR /workspace
COPY . /workspace
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package rhetorica-sandbox --no-dev \
    && mkdir -p /opt/rhetorica-sandbox /var/lib/rhetorica-sandbox/sessions \
    && cp /workspace/apps/sandbox/inspect_runner.py /opt/rhetorica-sandbox/inspect_runner.py \
    && chmod 0555 /opt/rhetorica-sandbox/inspect_runner.py
EXPOSE 8090
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/rhetorica-venv/bin/uvicorn", "rhetorica_sandbox.app:app", "--host", "0.0.0.0", "--port", "8090"]

FROM runtime AS prod
