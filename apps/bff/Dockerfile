# Build context: repository root (docker build --file apps/bff/Dockerfile .).
FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim AS base

COPY --from=uv /uv /uvx /bin/
ENV UV_LINK_MODE=copy
WORKDIR /workspace/apps/bff
COPY packages/contracts /workspace/packages/contracts
COPY agents/restaurant/src/restaurant_agent/pyproject.toml agents/restaurant/src/restaurant_agent/uv.toml /workspace/agents/restaurant/src/restaurant_agent/
COPY agents/restaurant/src/restaurant_agent/restaurant_agent /workspace/agents/restaurant/src/restaurant_agent/restaurant_agent
COPY apps/bff/pyproject.toml apps/bff/uv.lock ./

FROM base AS test
RUN uv sync --frozen --all-groups --no-install-project
COPY apps/bff/src ./src
COPY apps/bff/tests ./tests
RUN uv sync --frozen --all-groups
# The tests never call Foundry.
ENV BFF_WAITER=scripted
CMD ["/workspace/apps/bff/.venv/bin/python", "-m", "pytest"]

FROM base AS runtime
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/bff/src ./src
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 bff \
    && mkdir -p /data \
    && chown bff /data
ENV BFF_PORT=8000 \
    BFF_DATABASE_PATH=/data/bff.db \
    MEMORY_DATABASE_PATH=/data/memory.db
USER bff
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["/workspace/apps/bff/.venv/bin/python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('BFF_PORT', '8000') + '/healthz', timeout=3)"]
# One worker: turns run in-process and the state lives in SQLite.
CMD ["sh", "-c", "exec /workspace/apps/bff/.venv/bin/uvicorn bff.main:create_app --factory --host 0.0.0.0 --port \"$BFF_PORT\" --workers 1"]
