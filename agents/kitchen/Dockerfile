FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim AS base

COPY --from=uv /uv /uvx /bin/

WORKDIR /workspace/agents/kitchen/src/kitchen_agent
COPY packages/contracts /workspace/packages/contracts
COPY packages/observability /workspace/packages/observability
COPY agents/kitchen/src/kitchen_agent/pyproject.toml agents/kitchen/src/kitchen_agent/uv.lock agents/kitchen/src/kitchen_agent/uv.toml ./

FROM base AS test
RUN uv sync --frozen --all-groups --no-install-project
COPY agents/kitchen/src/kitchen_agent/kitchen_agent ./kitchen_agent
COPY agents/kitchen/src/kitchen_agent/tests ./tests
COPY agents/kitchen/src/kitchen_agent/main.py ./
CMD ["/workspace/agents/kitchen/src/kitchen_agent/.venv/bin/python", "-m", "pytest"]

FROM base AS runtime
RUN uv sync --frozen --no-dev --no-install-project
COPY agents/kitchen/src/kitchen_agent/kitchen_agent ./kitchen_agent
COPY agents/kitchen/src/kitchen_agent/main.py ./
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 kitchen \
    && chown -R kitchen:kitchen /workspace

ENV PORT=8089
USER kitchen
EXPOSE 8089
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["/workspace/agents/kitchen/src/kitchen_agent/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8089/health', timeout=3)"]
CMD ["/workspace/agents/kitchen/src/kitchen_agent/.venv/bin/python", "main.py"]
