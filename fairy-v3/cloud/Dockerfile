# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.11.28-python3.13-trixie-slim AS source

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY core /app/core
COPY capabilities /app/capabilities
COPY cloud /app/cloud
COPY sandbox/runner/fairy_sandbox_runner.py /opt/fairy/fairy_sandbox_runner.py
WORKDIR /app/cloud

FROM source AS runtime

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable
RUN groupadd --gid 10001 fairy \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin fairy \
    && install -o root -g root -m 0755 /opt/fairy/fairy_sandbox_runner.py /usr/local/bin/fairy-sandbox-runner \
    && mkdir -p /var/lib/fairy/core /var/lib/fairy-sandbox \
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
