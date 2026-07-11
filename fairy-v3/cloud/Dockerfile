# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.11.28-python3.13-trixie-slim AS source

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY core /app/core
COPY capabilities /app/capabilities
COPY cloud /app/cloud
WORKDIR /app/cloud

FROM source AS runtime

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable
RUN groupadd --gid 10001 fairy \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin fairy \
    && mkdir -p /var/lib/fairy/core \
    && chown -R 10001:10001 /app /var/lib/fairy

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
