# syntax=docker/dockerfile:1.7

FROM node:24.18.0-trixie-slim@sha256:366fdef91728b1b7fa18c84fba63b6e79ed77b7e10cc206878e9705da4d7b169 AS node-toolchain

FROM ghcr.io/astral-sh/uv:0.11.28-python3.13-trixie-slim AS source

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=node-toolchain /usr/local/ /usr/local/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bubblewrap ca-certificates cargo chromium git python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /usr/local/bin/yarn /usr/local/bin/yarnpkg \
    && npm install --global --ignore-scripts pnpm@10.34.4 yarn@1.22.22 \
    && test "$(node --version)" = "v24.18.0" \
    && test "$(yarn --version)" = "1.22.22" \
    && test "$(pnpm --version)" = "10.34.4"

WORKDIR /app
COPY core /app/core
COPY capabilities /app/capabilities
COPY cloud /app/cloud
COPY resources /app/resources
COPY sandbox/runner/fairy_sandbox_runner.py /opt/fairy/fairy_sandbox_runner.py
COPY sandbox/runner/fairy_runtime_supervisor.py /opt/fairy/fairy_runtime_supervisor.py
WORKDIR /app/cloud

FROM source AS runtime

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable
RUN groupadd --gid 10001 fairy \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin fairy \
    && install -o root -g root -m 0755 /opt/fairy/fairy_sandbox_runner.py /usr/local/bin/fairy-sandbox-runner \
    && install -o root -g root -m 0755 /opt/fairy/fairy_runtime_supervisor.py /usr/local/bin/fairy-runtime-supervisor \
    && mkdir -p /var/lib/fairy/core /var/lib/fairy-sandbox/dependencies /var/lib/fairy-sandbox/runtimes \
    && chown -R 10001:10001 /app /var/lib/fairy /var/lib/fairy-sandbox

ENV PATH="/app/cloud/.venv/bin:$PATH" \
    HOME=/tmp

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "fairy_cloud.main"]

FROM source AS test

RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked
RUN groupadd --gid 10001 fairy \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin fairy \
    && chown -R 10001:10001 /app

ENV PATH="/app/cloud/.venv/bin:$PATH" \
    HOME=/tmp

USER 10001:10001
CMD ["pytest"]
