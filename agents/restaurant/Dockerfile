FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim AS base

COPY --from=uv /uv /uvx /bin/

WORKDIR /workspace/agents/restaurant/src/restaurant_agent
COPY packages/contracts /workspace/packages/contracts
COPY agents/restaurant/src/restaurant_agent/pyproject.toml agents/restaurant/src/restaurant_agent/uv.lock agents/restaurant/src/restaurant_agent/uv.toml ./

FROM base AS test
RUN uv sync --frozen --all-groups --no-install-project
COPY agents/restaurant/src/restaurant_agent/restaurant_agent ./restaurant_agent
COPY agents/restaurant/src/restaurant_agent/tests ./tests
COPY agents/restaurant/src/restaurant_agent/main.py ./
CMD ["/workspace/agents/restaurant/src/restaurant_agent/.venv/bin/python", "-m", "pytest"]

FROM base AS runtime
RUN uv sync --frozen --no-dev --no-install-project
COPY agents/restaurant/src/restaurant_agent/restaurant_agent ./restaurant_agent
COPY agents/restaurant/src/restaurant_agent/main.py ./
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 restaurant \
    && mkdir -p /data \
    && chown restaurant /data

ENV PORT=8088 \
    MEMORY_DATABASE_PATH=/data/memory.db
USER restaurant
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["/workspace/agents/restaurant/src/restaurant_agent/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/readiness', timeout=3)"]
CMD ["/workspace/agents/restaurant/src/restaurant_agent/.venv/bin/python", "main.py"]